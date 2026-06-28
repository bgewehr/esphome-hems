#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EEBus connectivity diagnostics for esphome-hems.

Tests:
  1. TCP port reachability (ports 4712 + 4713)
  2. TLS handshake and SHIP SKI extraction (SHA-1 of raw EC key bytes, per openeebus)
  3. mDNS _ship._tcp service advertisement
  4. SHIP CMI init handshake (requires pairing mode ON on the ESP32)
  5. ESPHome API sensor state readout

Usage:
  python tools/diagnostics/check_eebus.py [--host 192.168.178.24]

Requires: zeroconf  (pip install zeroconf)
Optional: cryptography (pip install cryptography) — for SHIP SKI extraction + test cert
Optional: aioesphomeapi (for sensor readout)

SHIP CMI test notes:
  - Press 'WP Pairing-Modus starten' on the ESP32 web UI BEFORE running the test.
  - K40RF must not be actively connected (it holds the single connection slot).
  - The test uses a persistent client cert stored in ~/.esphome/eebus_test_cert.pem.
    The ESP32 auto-trusts this cert's SKI during pairing mode; it auto-exits pairing
    mode once DataExchange is reached (by K40RF reconnecting after the test).
"""

import argparse
import os
import pathlib
import socket
import ssl
import struct
import hashlib

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


# ---------------------------------------------------------------------------
# SHIP SKI — matches openeebus tls_certificate_mbedtls.c::CalcSubjectKeyIdString
#
# Algorithm: SHA-1 of the raw EC public key bytes (04 || Qx || Qy, 65 bytes
# for P-256), obtained by stripping the full SPKI DER down to just the BIT
# STRING value minus the unused-bits byte.  This is RFC 5280 Method 1, which
# is what mbedTLS generates in the SubjectKeyIdentifier extension.
# ---------------------------------------------------------------------------

def extract_ship_ski(der: bytes) -> str | None:
    """Return the SHIP SKI for a DER-encoded X.509 certificate.

    Mirrors openeebus CalcSubjectKeyIdStringWithBuf: SHA-1 of the raw EC
    public key bytes (04 || Qx || Qy) extracted from the SPKI BIT STRING.
    """
    try:
        from cryptography.x509 import load_der_x509_certificate
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        cert = load_der_x509_certificate(der)
        # X9.62 uncompressed point = 04 || Qx || Qy (65 bytes for P-256)
        raw_key = cert.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return hashlib.sha1(raw_key).hexdigest()
    except ImportError:
        pass

    # Fallback without 'cryptography': the raw EC point is the last 65 bytes
    # of the 91-byte EC P-256 SPKI DER (04 || Qx || Qy after BIT STRING header).
    ec_p256_oid = bytes.fromhex("30130607" "2a8648ce3d020106" "082a8648ce3d030107".replace(" ", ""))
    idx = der.find(ec_p256_oid)
    if idx >= 2 and der[idx - 2] == 0x30 and der[idx - 1] == 0x59:
        # SPKI starts at idx-2, total 91 bytes; raw key = last 65 bytes
        raw_key = der[idx - 2 + 26 : idx - 2 + 91]  # skip 26-byte header
        if len(raw_key) == 65 and raw_key[0] == 0x04:
            return hashlib.sha1(raw_key).hexdigest()
    return None


# Keep old name as alias so nothing else breaks
extract_spki_ski = extract_ship_ski


# ---------------------------------------------------------------------------
# Persistent test client certificate
# ---------------------------------------------------------------------------

_CERT_DIR = pathlib.Path.home() / ".esphome"
_CERT_FILE = _CERT_DIR / "eebus_test_cert.pem"
_KEY_FILE = _CERT_DIR / "eebus_test_key.pem"


def _make_test_cert() -> tuple[bytes, bytes] | None:
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        import datetime
    except ImportError:
        return None

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "eebus-test-client")])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        # SubjectKeyIdentifier extension so ESP32 (openeebus) can extract SKI
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _ski_from_pem(cert_pem: bytes) -> str:
    try:
        from cryptography.x509 import load_pem_x509_certificate
        from cryptography.hazmat.primitives import serialization
        cert = load_pem_x509_certificate(cert_pem)
        der = cert.public_bytes(serialization.Encoding.DER)
        return extract_ship_ski(der) or "unknown"
    except Exception:
        return "unknown"


def load_or_create_test_cert() -> tuple[pathlib.Path, pathlib.Path, str] | None:
    """Return (cert_path, key_path, ski).

    Generates a persistent EC P-256 self-signed cert on first call.
    Returns None if 'cryptography' is not installed.
    """
    if _CERT_FILE.exists() and _KEY_FILE.exists():
        ski = _ski_from_pem(_CERT_FILE.read_bytes())
        return _CERT_FILE, _KEY_FILE, ski

    result = _make_test_cert()
    if result is None:
        return None
    cert_pem, key_pem = result
    _CERT_DIR.mkdir(parents=True, exist_ok=True)
    _CERT_FILE.write_bytes(cert_pem)
    _KEY_FILE.write_bytes(key_pem)
    ski = _ski_from_pem(cert_pem)
    return _CERT_FILE, _KEY_FILE, ski


# ---------------------------------------------------------------------------
# Test 1: TCP port reachability
# ---------------------------------------------------------------------------

def check_port(host: str, port: int, label: str, timeout: float = 3.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        ok(f"{label} port {port} — open")
        return True
    except (OSError, TimeoutError) as e:
        fail(f"{label} port {port} — {e}")
        return False


# ---------------------------------------------------------------------------
# Test 2: TLS handshake + SHIP SKI
# ---------------------------------------------------------------------------

def check_tls(host: str, port: int, label: str, timeout: float = 5.0) -> str | None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
        conn = ctx.wrap_socket(raw, server_hostname=host)
        der = conn.getpeercert(binary_form=True)
        conn.close()
        if not der:
            fail(f"{label} TLS — no certificate returned")
            return None
        ski = extract_spki_ski(der)
        if ski:
            ok(f"{label} TLS OK — SHIP SKI: {ski}")
        else:
            sha1 = hashlib.sha1(der).hexdigest()
            ok(f"{label} TLS OK — cert SHA1: {sha1} (install 'cryptography' for proper SHIP SKI)")
            ski = sha1
        return ski
    except Exception as e:
        fail(f"{label} TLS handshake — {e}")
        return None


# ---------------------------------------------------------------------------
# Test 3: mDNS _ship._tcp browse
# ---------------------------------------------------------------------------

def check_mdns(timeout: float = 8.0) -> list[dict]:
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        warn("mDNS test skipped — install 'zeroconf' package (pip install zeroconf)")
        return []

    found: list[dict] = []

    def on_found(zeroconf, service_type, name, state_change):
        info = zeroconf.get_service_info(service_type, name)
        if info:
            props = {k.decode(): v.decode() if isinstance(v, bytes) else v
                     for k, v in info.properties.items()}
            found.append({
                "name": info.name,
                "host": info.server,
                "port": info.port,
                "ski": props.get("ski", ""),
                "brand": props.get("brand", ""),
                "model": props.get("model", ""),
                "register": props.get("register", ""),
            })

    zc = Zeroconf()
    ServiceBrowser(zc, "_ship._tcp.local.", handlers=[on_found])
    import time
    time.sleep(timeout)
    zc.close()

    if found:
        for svc in found:
            ok(f"mDNS _ship._tcp: {svc['name']} @ {svc['host']}:{svc['port']}")
            print(f"       SKI={svc['ski']}")
            print(f"       brand={svc['brand']} model={svc['model']} register={svc['register']}")
    else:
        warn("mDNS: No _ship._tcp services found (may be normal if PC is on a different subnet)")

    return found


# ---------------------------------------------------------------------------
# Test 4: SHIP CMI init handshake
# ---------------------------------------------------------------------------

def _ws_send_binary(conn: ssl.SSLSocket, data: bytes) -> None:
    """Send a masked WebSocket binary frame (client must mask per RFC 6455)."""
    mask = os.urandom(4)
    header = bytes([0x82, 0x80 | len(data)]) + mask
    masked = bytes([data[i] ^ mask[i % 4] for i in range(len(data))])
    conn.send(header + masked)


def _ws_recv_frame(conn: ssl.SSLSocket, timeout: float = 8.0) -> tuple[int, bytes] | None:
    """Receive one WebSocket frame; return (opcode, payload) or None on timeout/error."""
    conn.settimeout(timeout)
    try:
        h = conn.recv(2)
        if len(h) < 2:
            return None
        opcode = h[0] & 0x0F
        masked = (h[1] >> 7) & 1
        length = h[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", conn.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", conn.recv(8))[0]
        mask_key = conn.recv(4) if masked else b""
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        if masked:
            payload = bytes([payload[i] ^ mask_key[i % 4] for i in range(len(payload))])
        return opcode, payload
    except (socket.timeout, TimeoutError):
        return None
    except Exception:
        return None


def check_ship_cmi(
    host: str,
    port: int,
    label: str,
    cert_path: pathlib.Path | None = None,
    key_path: pathlib.Path | None = None,
    client_ski: str = "unknown",
    timeout: float = 8.0,
) -> bool:
    """WebSocket upgrade + SHIP CMI \\x00\\x00 handshake.

    Pairing mode must be active on the ESP32 ('WP Pairing-Modus starten' button)
    and no other device may be holding the connection slot.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if cert_path and key_path and cert_path.exists() and key_path.exists():
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

    try:
        raw = socket.create_connection((host, port), timeout=5.0)
        conn = ctx.wrap_socket(raw, server_hostname=host)
    except Exception as e:
        fail(f"{label} CMI: TLS connect failed — {e}")
        return False

    upgrade = (
        "GET /ship/ HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Protocol: ship\r\n"
        "\r\n"
    )
    try:
        conn.send(upgrade.encode())
        conn.settimeout(5.0)
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = conn.recv(512)
            if not chunk:
                break
            resp += chunk
    except Exception as e:
        conn.close()
        fail(f"{label} CMI: HTTP upgrade failed — {e}")
        return False

    resp_str = resp.decode(errors="replace")
    if "101" not in resp_str:
        conn.close()
        fail(f"{label} CMI: WebSocket refused — {resp_str.split(chr(13))[0]}")
        return False

    ok(f"{label} CMI: WebSocket accepted (client SKI={client_ski[:16]}…)")

    # Send SHIP CMI init message: binary \x00\x00
    _ws_send_binary(conn, b"\x00\x00")

    frame = _ws_recv_frame(conn, timeout=timeout)
    conn.close()

    if frame is None:
        fail(f"{label} CMI: no response within {timeout}s")
        warn("  Causes: K40RF is connected (one slot only) — wait for disconnect")
        warn("          OR press 'WP Pairing-Modus starten' and retry")
        return False

    opcode, payload = frame
    if opcode == 8:  # WS CLOSE
        fail(f"{label} CMI: server closed connection immediately")
        warn("  → Connection rejected. Either:")
        warn("    1. K40RF already connected — no free connection slot")
        warn("    2. Pairing mode not active — press 'WP Pairing-Modus starten'")
        warn(f"   3. SKI {client_ski[:16]}… not trusted (fixed by pairing mode)")
        return False

    if opcode == 2 and payload == b"\x00\x00":
        ok(f"{label} CMI: \\x00\\x00 response received — handshake complete!")
        return True

    warn(f"{label} CMI: unexpected frame opcode={opcode} data={payload.hex()[:16]}")
    return False


# ---------------------------------------------------------------------------
# Test 5: ESPHome API
# ---------------------------------------------------------------------------

def check_esphome_api(host: str, port: int = 6053) -> None:
    try:
        import asyncio
        import aioesphomeapi
    except ImportError:
        warn("API sensor test skipped — install 'aioesphomeapi' package")
        return

    async def _read():
        cli = aioesphomeapi.APIClient(host, port, "")
        try:
            await cli.connect(login=True)
        except Exception as e:
            fail(f"ESPHome API connect — {e}")
            return
        entities, _ = await cli.list_entities_services()
        eebus = [e for e in entities
                 if any(kw in (getattr(e, "name", "") or "").lower()
                        for kw in ("eebus", "ski", "pairing", "wp "))]
        if eebus:
            ok(f"ESPHome API: {len(eebus)} EEBus entities")
            for e in eebus:
                print(f"       {e.name}")
        else:
            warn("ESPHome API: no EEBus entities found")
        await cli.disconnect()

    asyncio.run(_read())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="EEBus diagnostics for esphome-hems")
    parser.add_argument("--host", default="192.168.178.24")
    parser.add_argument("--lpc-port", type=int, default=4712)
    parser.add_argument("--wp-port", type=int, default=4713)
    parser.add_argument("--skip-mdns", action="store_true")
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--skip-cmi", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  EEBus Diagnostics — {args.host}")
    print(f"{'='*60}\n")

    # Test client certificate (persistent across runs → stable SKI)
    cert_info = load_or_create_test_cert()
    if cert_info:
        cert_path, key_path, client_ski = cert_info
        ok(f"Client cert SKI (test): {client_ski}")
        print(f"       Path: {cert_path}")
        print(f"       This SKI is registered on the ESP32 when pairing mode is active.")
        print(f"       Press 'WP Pairing-Modus starten' on the ESP32, then run the CMI test.")
    else:
        cert_path = key_path = None
        client_ski = "unknown"
        warn("No client cert — install 'cryptography' (pip install cryptography) for a stable test SKI")
    print()

    print("[1] TCP Port Reachability")
    check_port(args.host, 80, "Web Server")
    lpc_ok = check_port(args.host, args.lpc_port, "SHIP LPC")
    wp_ok = check_port(args.host, args.wp_port, "SHIP WP")
    print()

    print("[2] TLS Certificate & SHIP SKI")
    server_ski_lpc = None
    server_ski_wp = None
    if lpc_ok:
        server_ski_lpc = check_tls(args.host, args.lpc_port, "SHIP LPC")
    if wp_ok:
        server_ski_wp = check_tls(args.host, args.wp_port, "SHIP WP")
    print()

    if not args.skip_mdns:
        print("[3] mDNS _ship._tcp Service Discovery")
        mdns_found = check_mdns()
        if mdns_found:
            # Match each discovered service by port to the cert SKI we computed
            port_ski = {args.lpc_port: server_ski_lpc, args.wp_port: server_ski_wp}
            for svc in mdns_found:
                expected = port_ski.get(svc["port"])
                if expected and svc["ski"] == expected:
                    ok(f"mDNS SKI matches cert SKI for port {svc['port']}")
                elif expected and svc["ski"]:
                    warn(f"mDNS SKI {svc['ski'][:16]}… ≠ cert SKI {expected[:16]}… (port {svc['port']})")
                    warn(f"  → This is normal if the cert was recently regenerated")
        print()

    if not args.skip_cmi:
        print("[4] SHIP CMI Init Handshake")
        print("     Prerequisite: press 'WP Pairing-Modus starten' on the ESP32 web UI.")
        print("     K40RF must not be actively connected (holds the single connection slot).")
        if wp_ok:
            check_ship_cmi(args.host, args.wp_port, "SHIP WP",
                           cert_path=cert_path, key_path=key_path, client_ski=client_ski)
        if lpc_ok:
            check_ship_cmi(args.host, args.lpc_port, "SHIP LPC",
                           cert_path=cert_path, key_path=key_path, client_ski=client_ski)
        print()

    if not args.skip_api:
        print("[5] ESPHome API Entity Check")
        check_esphome_api(args.host)
        print()

    print(f"{'='*60}")
    if lpc_ok and wp_ok:
        print(f"  {GREEN}Both SHIP servers reachable.{RESET}")
        print(f"  Starte die EEBus-Suche am Waermepumpen-Display.")
    elif wp_ok:
        print(f"  {YELLOW}WP OK, LPC not reachable.{RESET}")
    elif lpc_ok:
        print(f"  {RED}LPC OK but WP not reachable!{RESET}")
    else:
        print(f"  {RED}Neither SHIP server is reachable!{RESET}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
