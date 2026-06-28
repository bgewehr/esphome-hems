#!/usr/bin/env python3
"""
fake_steuerbox.py — EEBus §14a Steuerbox pairing/LPC test.

Connects to esphome-hems eebus_lpc port (4712) as a fake Steuerbox/EG,
completes the full SHIP handshake, and optionally sends an LPC power limit.

Requires:  pip install cryptography

Usage:
  python tools/fake_steuerbox.py 192.168.178.24
  python tools/fake_steuerbox.py 192.168.178.24 --limit 4200

Before connecting:
  1. Open HEMS web UI at http://<hems-ip>
  2. Press "CS Pairing-Modus starten" to open the 5-min pairing window
  3. Run this script — it will print the fake SKI, then connect
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import tempfile


# ── Certificate ──────────────────────────────────────────────────────────────

def generate_cert():
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from datetime import datetime, timezone, timedelta
    except ImportError:
        print("ERROR: pip install cryptography")
        sys.exit(1)

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FakeSteuerbox")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    # SKI = SHA-1 of raw EC point bytes (uncompressed: 04 || x || y).
    # openeebus CalcSubjectKeyIdString parses the SubjectPublicKeyInfo DER and
    # strips: outer SEQUENCE, AlgorithmIdentifier, BIT STRING header, unused-bits byte.
    # What remains for P-256 is the raw 65-byte uncompressed point → X962 encoding.
    raw_point = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    ski = hashlib.sha1(raw_point).hexdigest()

    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        ski,
    )


# ── Raw WebSocket (client, binary frames only) ────────────────────────────────

def ws_upgrade(sock, host, port):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET /ship/ HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: ship\r\n"
        f"\r\n"
    )
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("No response to WebSocket upgrade")
        resp += chunk
    if b" 101 " not in resp:
        raise ConnectionError(
            f"WebSocket upgrade failed:\n{resp.decode(errors='replace')[:400]}"
        )


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by server")
        buf += chunk
    return buf


def ws_send(sock, data: bytes):
    """Send a masked binary WebSocket frame (client MUST mask)."""
    mask = os.urandom(4)
    plen = len(data)
    if plen < 126:
        hdr = bytes([0x82, 0x80 | plen])
    elif plen < 0x10000:
        hdr = bytes([0x82, 0xFE]) + struct.pack(">H", plen)
    else:
        hdr = bytes([0x82, 0xFF]) + struct.pack(">Q", plen)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(hdr + mask + masked)


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
        code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 0
        raise ConnectionError(f"Server sent WebSocket close (code {code})")
    if opcode == 0x09:  # ping
        # send pong
        ws_send(sock, payload)
        return ws_recv(sock)
    return payload


# ── SHIP protocol helpers ─────────────────────────────────────────────────────

MSG_CTRL = 0x01
MSG_DATA = 0x02


def ship_send(sock, payload: dict):
    raw = bytes([MSG_CTRL]) + json.dumps(payload, separators=(",", ":")).encode()
    print(f"  → {json.dumps(payload)}")
    ws_send(sock, raw)


def ship_recv(sock) -> dict:
    raw = ws_recv(sock)
    msg_type, body = raw[0], raw[1:]
    parsed = json.loads(body)
    tag = {MSG_CTRL: "CTRL", MSG_DATA: "DATA"}.get(msg_type, f"0x{msg_type:02x}")
    print(f"  ← [{tag}] {json.dumps(parsed)}")
    return parsed


# ── SHIP handshake (fake Steuerbox = SHIP client, HEMS = SHIP server) ─────────

def ship_handshake(sock, local_ship_id: str) -> str:
    """Runs the full SHIP handshake. Returns the remote (HEMS) SHIP ID."""

    # 1. CMI Init — client sends first, server responds with same
    print("\n[1/5] CMI Init")
    ws_send(sock, bytes([0x00, 0x00]))
    init = ws_recv(sock)
    if init[:2] != bytes([0x00, 0x00]):
        raise ValueError(f"Unexpected CMI init response: {init.hex()}")
    print("      OK")

    # 2. SME Hello — both sides independently send ready; both receive ready → OK
    print("\n[2/5] Hello")
    ship_send(sock, {"connectionHello": [{"phase": "ready"}, {"waiting": 60000}]})
    resp = ship_recv(sock)
    entries = resp.get("connectionHello", [])
    phase = next((e.get("phase") for e in entries if "phase" in e), None)
    if phase != "ready":
        raise ValueError(f"Hello: expected phase=ready, got {phase!r}")
    print("      OK")

    # 3. Protocol handshake — client sends announceMax, server sends select, client confirms
    print("\n[3/5] Protocol Handshake")
    proto_ver = {"version": [{"major": 1}, {"minor": 0}]}
    proto_fmt = {"formats": [{"format": ["JSON-UTF8"]}]}
    ship_send(sock, {"messageProtocolHandshake": [
        {"handshakeType": "announceMax"}, proto_ver, proto_fmt
    ]})
    resp = ship_recv(sock)
    hs = resp.get("messageProtocolHandshake", [])
    hs_type = next((e.get("handshakeType") for e in hs if "handshakeType" in e), None)
    if hs_type != "select":
        raise ValueError(f"Protocol handshake: expected select, got {hs_type!r}")
    ship_send(sock, {"messageProtocolHandshake": [
        {"handshakeType": "select"}, proto_ver, proto_fmt
    ]})
    print("      OK")

    # 4. PIN state — both sides send "none" simultaneously, both receive "none"
    #    We send immediately after select; HEMS sends "none" right after our select arrives.
    print("\n[4/5] PIN State")
    ship_send(sock, {"connectionPinState": [{"pinState": "none"}]})
    resp = ship_recv(sock)
    entries = resp.get("connectionPinState", [])
    pin = next((e.get("pinState") for e in entries if "pinState" in e), None)
    if pin not in ("none", "pinOk"):
        raise ValueError(f"PIN state: unexpected value {pin!r}")
    print("      OK → kSmeStateApproved (state 37) → kDataExchange (state 38) entered on HEMS")

    # 5. Access methods — both sides send accessMethodsRequest on DataExchange entry.
    #    HEMS sends its request immediately, so the first recv after PIN gets it.
    print("\n[5/5] Access Methods")
    server_id = "?"
    ship_send(sock, {"accessMethodsRequest": []})
    for _ in range(6):
        resp = ship_recv(sock)
        if "accessMethodsRequest" in resp:
            ship_send(sock, {"accessMethods": [{"id": local_ship_id}]})
        elif "accessMethods" in resp:
            entries = resp.get("accessMethods", [])
            server_id = next((e.get("id") for e in entries if "id" in e), "?")
            break
    print(f"      OK  — HEMS SHIP ID: {server_id!r}")

    return server_id


# ── Optional: send SPINE LPC power limit ─────────────────────────────────────

def send_lpc_limit(sock, limit_w: float):
    """Send a §14a LPC power limit via SPINE over the established SHIP connection."""
    print(f"\n[→] Sending LPC limit: {limit_w:.0f} W")
    # SPINE CmdType loadControlLimitListData, limitId=0, scale=0 (value in W)
    spine = json.dumps({
        "cmd": [{
            "loadControlLimitListData": {
                "loadControlLimitData": [{
                    "limitId": 0,
                    "isLimitChangeable": True,
                    "isLimitActive": True,
                    "value": {"number": int(limit_w), "scale": 0},
                }]
            }
        }]
    })
    msg = {"data": [
        {"header": [{"protocolId": "ee1.0"}]},
        {"payload": spine},
    ]}
    raw = bytes([MSG_DATA]) + json.dumps(msg, separators=(",", ":")).encode()
    print(f"  → DATA: {json.dumps(msg)}")
    ws_send(sock, raw)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="EEBus fake Steuerbox (§14a EG) pairing/LPC test"
    )
    ap.add_argument("host", help="HEMS IP address, e.g. 192.168.178.24")
    ap.add_argument("--port", type=int, default=4712, help="eebus_lpc SHIP port (default 4712)")
    ap.add_argument(
        "--limit", type=float, default=None,
        help="After pairing: send this LPC power limit in W (e.g. 4200)",
    )
    ap.add_argument(
        "--no-pause", action="store_true",
        help="Skip the 'press Enter' prompts (useful for automation)",
    )
    args = ap.parse_args()

    print("=" * 60)
    print("EEBus Fake Steuerbox Test")
    print("=" * 60)

    print("\nGenerating self-signed TLS certificate (EC P-256)...")
    cert_pem, key_pem, ski = generate_cert()
    print(f"Fake Steuerbox SKI: {ski}")

    print(f"""
Prerequisites:
  1. Open http://{args.host} in a browser
  2. In the EEBus section, press  "CS Pairing-Modus starten"
  3. Continue here — the script will connect and complete the SHIP handshake
  4. HEMS should show the SKI above as "CS Pairing ausstehend"
  5. Press "CS Pairing akzeptieren" to persist the trust (optional — SHIP
     handshake reaches kDataExchange (state 38) automatically without needing this)
""")

    if not args.no_pause:
        input("Press Enter once pairing mode is active on the HEMS...")

    # Write cert/key to temp files (ssl module needs file paths)
    with (
        tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cf,
        tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as kf,
    ):
        cf.write(cert_pem)
        cert_file = cf.name
        kf.write(key_pem)
        key_file = kf.name

    sock = None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE   # EEBus uses self-signed certs — no CA chain
        ctx.load_cert_chain(cert_file, key_file)   # present our client cert

        print(f"\nConnecting to {args.host}:{args.port}...")
        raw = socket.create_connection((args.host, args.port), timeout=15)
        sock = ctx.wrap_socket(raw, server_hostname=args.host)
        print(f"TLS established  ({sock.version()})")

        ws_upgrade(sock, args.host, args.port)
        print("WebSocket upgraded  (/ship/  subprotocol=ship)")

        local_id = f"FakeSteuerbox-{ski[:8]}"
        server_id = ship_handshake(sock, local_id)

        print(f"""
+----------------------------------------------------------+
|  PAIRING COMPLETE -- kDataExchange (state 38) reached!   |
|                                                          |
|  Our SKI  : {ski[:48]:<48} |
|  Server ID: {server_id:<48} |
+----------------------------------------------------------+
""")

        print("HEMS web UI should now show:")
        print(f"  CS Verbindungsstatus : Gepairt: {ski}")
        print(f"  CS Gepaarte SKI      : {ski}")

        if args.limit is not None:
            if not args.no_pause:
                input("\nPress Enter to send the LPC limit command...")
            send_lpc_limit(sock, args.limit)
            print(f"\nLPC limit sent: {args.limit:.0f} W")
            print("Check HEMS log for 'EEBUS LPC limit active'")

        print("\nConnection open. Press Enter to disconnect cleanly.")
        if not args.no_pause:
            input()

    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as exc:
        import traceback
        print(f"\nERROR: {exc}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        for f in (cert_file, key_file):
            try:
                os.unlink(f)
            except Exception:
                pass


if __name__ == "__main__":
    main()
