#!/usr/bin/env python3
"""
test_ski_extraction.py — Autonomous TLS peer cert SKI extraction test.

1. Activates pairing mode on both LPC (CS) and WP ports
2. Connects with a self-signed EC cert and completes the SHIP handshake
3. Monitors HEMS SSE stream to verify SKI was extracted correctly

PASS: "WP Verbindungsstatus" sensor shows "Gepairt: <our_ski>"
      AND "Peer SKI:" appears in the HEMS HTTP log (visible in ESPHome log stream)
FAIL: Sensor shows "unknown" or handshake fails before DataExchange

Usage:
  python tools/test_ski_extraction.py 192.168.178.24
"""

import argparse, base64, hashlib, json, os, queue, socket, ssl, struct
import sys, tempfile, threading, time


# ── TLS cert generation ───────────────────────────────────────────────────────

def generate_cert():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from datetime import datetime, timezone, timedelta

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TestSKI")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    raw_point = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ski = hashlib.sha1(raw_point).hexdigest()
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()),
        ski,
    )


# ── ESPHome SSE monitoring ─────────────────────────────────────────────────────

def sse_monitor(host: str, duration_s: float) -> list[dict]:
    """Read ESPHome SSE /events stream for duration_s seconds. Returns list of data dicts."""
    events = []
    try:
        s = socket.create_connection((host, 80), timeout=5)
        s.sendall((
            f"GET /events HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Accept: text/event-stream\r\n"
            f"Cache-Control: no-cache\r\n\r\n"
        ).encode())
        # skip HTTP headers
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += s.recv(4096)
        s.settimeout(0.5)
        end = time.monotonic() + duration_s
        while time.monotonic() < end:
            try:
                buf += s.recv(4096)
            except socket.timeout:
                pass
        s.close()
        for line in buf.decode(errors="replace").splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"  [SSE] {e}")
    return events


def get_sse_value(host: str, name_id: str, timeout_s: float = 8) -> str | None:
    """Poll SSE stream until name_id has a value, or timeout."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        for ev in sse_monitor(host, min(2, end - time.monotonic())):
            if ev.get("name_id") == name_id and "value" in ev:
                return ev["value"]
    return None


# ── ESPHome web server button press ──────────────────────────────────────────

def press_button(host: str, name: str) -> bool:
    """Press an ESPHome button by its full entity name."""
    # ESPHome web server v3 REST API: POST /name_id/press
    # where name_id is "button/<full entity name>"
    import urllib.parse
    path = f"/button/{urllib.parse.quote(name, safe='')}/press"
    try:
        s = socket.create_connection((host, 80), timeout=5)
        req = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        s.sendall(req.encode())
        resp = b""
        s.settimeout(2)
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
        except socket.timeout:
            pass
        s.close()
        ok = b" 200 " in resp or b" 204 " in resp
        status = resp.split(b"\r\n")[0].decode(errors="replace") if resp else "no response"
        print(f"  [button] {name[:50]}: {status}")
        return ok
    except Exception as e:
        print(f"  [button] ERROR pressing '{name}': {e}")
        return False


# ── WebSocket + SHIP ──────────────────────────────────────────────────────────

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf


def ws_upgrade(sock, host, port):
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((
        f"GET /ship/ HTTP/1.1\r\nHost: {host}:{port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: ship\r\n\r\n"
    ).encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)
    if b" 101 " not in resp:
        raise ConnectionError(f"WS upgrade failed: {resp[:200]}")


def ws_send(sock, data: bytes):
    mask = os.urandom(4)
    plen = len(data)
    hdr = bytes([0x82, 0x80 | plen]) if plen < 126 else bytes([0x82, 0xFE]) + struct.pack(">H", plen)
    sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def ws_recv(sock) -> bytes:
    hdr = _recv_exact(sock, 2)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    plen = hdr[1] & 0x7F
    if plen == 126:
        plen = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif plen == 127:
        plen = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    mask_key = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, plen)
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    if opcode == 0x08:
        raise ConnectionError("Server sent WS close")
    return payload


def ship_send(sock, payload: dict):
    ws_send(sock, bytes([0x01]) + json.dumps(payload, separators=(",", ":")).encode())


def ship_recv(sock) -> dict:
    raw = ws_recv(sock)
    return json.loads(raw[1:])


def ship_handshake(sock, local_id: str):
    """Full SHIP handshake through DataExchange (state 38)."""
    # 1. CMI Init
    ws_send(sock, bytes([0x00, 0x00]))
    init = ws_recv(sock)
    assert init[:2] == bytes([0x00, 0x00]), f"Bad CMI: {init.hex()}"

    # 2. Hello
    ship_send(sock, {"connectionHello": [{"phase": "ready"}, {"waiting": 60000}]})
    resp = ship_recv(sock)
    entries = resp.get("connectionHello", [])
    phase = next((e.get("phase") for e in entries if "phase" in e), None)
    assert phase == "ready", f"Hello phase: {phase!r}"

    # 3. Protocol handshake
    pv = {"version": [{"major": 1}, {"minor": 0}]}
    pf = {"formats": [{"format": ["JSON-UTF8"]}]}
    ship_send(sock, {"messageProtocolHandshake": [{"handshakeType": "announceMax"}, pv, pf]})
    resp = ship_recv(sock)
    hs = resp.get("messageProtocolHandshake", [])
    ht = next((e.get("handshakeType") for e in hs if "handshakeType" in e), None)
    assert ht == "select", f"Protocol HS: {ht!r}"
    ship_send(sock, {"messageProtocolHandshake": [{"handshakeType": "select"}, pv, pf]})

    # 4. PIN state
    ship_send(sock, {"connectionPinState": [{"pinState": "none"}]})
    ship_recv(sock)

    # 5. Access Methods (DataExchange)
    ship_send(sock, {"accessMethodsRequest": []})
    for _ in range(8):
        resp = ship_recv(sock)
        if "accessMethodsRequest" in resp:
            ship_send(sock, {"accessMethods": [{"id": local_id}]})
        elif "accessMethods" in resp:
            return
    raise TimeoutError("Did not reach DataExchange")


# ── Test runner ───────────────────────────────────────────────────────────────

def run_test(host: str, port: int, label: str,
             ctx: ssl.SSLContext, ski: str, hold_s: float = 8.0) -> bool:
    sock = None
    try:
        raw = socket.create_connection((host, port), timeout=10)
        sock = ctx.wrap_socket(raw, server_hostname=host)
        print(f"    TLS connected ({sock.version()})")
        ws_upgrade(sock, host, port)
        print(f"    WebSocket upgraded")
        ship_handshake(sock, f"TestSKI-{ski[:8]}")
        print(f"    DataExchange reached! Holding {hold_s:.0f}s for sensor poll...")
        time.sleep(hold_s)
        sock.close()
        sock = None
        return True
    except Exception as e:
        print(f"    {type(e).__name__}: {e}")
        return False
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", default="192.168.178.24")
    ap.add_argument("--lpc-port", type=int, default=4712)
    ap.add_argument("--wp-port",  type=int, default=4713)
    args = ap.parse_args()
    host = args.host

    print("=" * 60)
    print("HEMS SKI Extraction Test")
    print("=" * 60)

    print("\nGenerating test certificate...")
    cert_pem, key_pem, ski = generate_cert()
    print(f"Test cert SKI: {ski}")

    cert_file = key_file = None
    results = {}
    try:
        with (
            tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cf,
            tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as kf,
        ):
            cf.write(cert_pem)
            cert_file = cf.name
            kf.write(key_pem)
            key_file = kf.name

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.load_cert_chain(cert_file, key_file)

        # ── LPC / CS (port 4712) ──────────────────────────────────────────────
        print("\n--- CS/LPC (port 4712) ---")
        print("  Activating CS pairing mode...")
        press_button(host, "ESPHome HEMS CS Pairing-Modus starten")
        time.sleep(2)

        # Monitor SSE in background while connecting
        sse_q: queue.Queue[list[dict]] = queue.Queue()
        def sse_thread():
            sse_q.put(sse_monitor(host, 12))
        t = threading.Thread(target=sse_thread, daemon=True)
        t.start()

        time.sleep(1)
        print("  Connecting to LPC port...")
        lpc_ok = run_test(host, args.lpc_port, "LPC", ctx, ski)

        t.join(timeout=15)
        events = sse_q.get_nowait() if not sse_q.empty() else []

        # Check SSE for our SKI in "CS Pairing ausstehend" or "CS Verbindungsstatus"
        # Check sensors: "CS Pairing ausstehend" should show our SKI
        # "CS Verbindungsstatus" should show "Gepairt & verbunden: <ski>"
        lpc_ski_seen = any(
            ski[:20] in str(ev.get("value", ""))
            for ev in events
            if "cs" in str(ev.get("name_id", "")).lower()
        )
        if lpc_ski_seen:
            print(f"  SSE confirms: HEMS saw our SKI in CS sensors!")
        else:
            print(f"  SSE: our SKI not found in CS sensor updates")
            for ev in events:
                if "cs" in str(ev.get("name_id", "")).lower() and "value" in ev:
                    print(f"    {ev.get('name_id','?')}: {str(ev.get('value','?'))[:80]}")

        results["LPC"] = lpc_ok and lpc_ski_seen

        # ── WP (port 4713) ────────────────────────────────────────────────────
        print("\n--- WP (port 4713) ---")
        print("  Activating WP pairing mode...")
        press_button(host, "ESPHome HEMS WP Pairing-Modus starten")
        time.sleep(2)

        sse_q2: queue.Queue[list[dict]] = queue.Queue()
        def sse_thread2():
            sse_q2.put(sse_monitor(host, 20))  # 20s to cover 8s hold + sensor poll
        t2 = threading.Thread(target=sse_thread2, daemon=True)
        t2.start()

        time.sleep(1)
        print("  Connecting to WP port...")
        wp_ok = run_test(host, args.wp_port, "WP", ctx, ski, hold_s=8.0)

        t2.join(timeout=25)
        events2 = sse_q2.get_nowait() if not sse_q2.empty() else []

        # "WP Verbindungsstatus" shows "Gepairt: <ski>" while connection is live
        # (poll interval 5s; hold_s=8s ensures at least one poll fires)
        wp_ski_seen = any(
            ski[:20] in str(ev.get("value", ""))
            for ev in events2
            if "wp" in str(ev.get("name_id", "")).lower()
        )
        if wp_ski_seen:
            print(f"  SSE confirms: HEMS saw our SKI in WP sensors!")
        else:
            print(f"  SSE: our SKI not found in WP sensor updates")
            for ev in events2:
                if "wp" in str(ev.get("name_id", "")).lower() and "value" in ev:
                    print(f"    {ev.get('name_id','?')}: {str(ev.get('value','?'))[:80]}")

        results["WP"] = wp_ok and wp_ski_seen

        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        all_ok = True
        for name, ok in results.items():
            status = "PASS" if ok else "FAIL"
            print(f"  {name}: {status}")
            if not ok:
                all_ok = False

        if all_ok:
            print(f"\nSUCCESS: SKI extraction working on both ports!")
            print(f"Our SKI ({ski}) was correctly identified by HEMS.")
        else:
            print(f"\nFAILED: Check HEMS log for 'eebus_http_srv' messages.")
            print(f"Expected: [eebus_http_srv] Peer SKI: {ski[:20]}...")
            print(f"Got 'unknown'? Fix: struct layout mismatch in http_server_esp32.c")

        sys.exit(0 if all_ok else 1)

    finally:
        for f in [cert_file, key_file]:
            if f:
                try:
                    os.unlink(f)
                except Exception:
                    pass


if __name__ == "__main__":
    main()
