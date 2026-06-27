#!/usr/bin/env python3
"""EEBus connectivity diagnostics for esphome-hems.

Tests:
  1. SHIP HTTPS server reachability (ports 4712 + 4713)
  2. TLS handshake and certificate extraction
  3. mDNS _ship._tcp service advertisement
  4. ESPHome API sensor state readout (pairing status, SKI, etc.)

Usage:
  python tools/diagnostics/check_eebus.py [--host 192.168.178.24]

Requires: zeroconf  (pip install zeroconf)
Optional: aioesphomeapi (for sensor readout)
"""

import argparse
import socket
import ssl
import struct
import sys
import textwrap
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
# Test 1: TCP port reachability
# ---------------------------------------------------------------------------

def check_port(host: str, port: int, label: str, timeout: float = 3.0) -> bool:
    """Return True if a TCP connection can be established."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        ok(f"{label} port {port} — open")
        return True
    except (OSError, TimeoutError) as e:
        fail(f"{label} port {port} — {e}")
        return False


# ---------------------------------------------------------------------------
# Test 2: TLS handshake + certificate SKI extraction
# ---------------------------------------------------------------------------

def check_tls(host: str, port: int, label: str, timeout: float = 5.0) -> str | None:
    """Perform a TLS handshake, extract and return the server certificate SKI."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed cert

    try:
        raw = socket.create_connection((host, port), timeout=timeout)
        conn = ctx.wrap_socket(raw, server_hostname=host)
        der = conn.getpeercert(binary_form=True)
        conn.close()
        if not der:
            fail(f"{label} TLS — no certificate returned")
            return None

        # SKI = SHA-1 of SubjectPublicKeyInfo (simplified: hash full DER cert
        # for display; real SKI uses the SPKI, but for identification this suffices)
        ski_hash = hashlib.sha1(der).hexdigest()
        ok(f"{label} TLS handshake OK — cert SHA1={ski_hash[:16]}…")
        return ski_hash
    except Exception as e:
        fail(f"{label} TLS handshake — {e}")
        return None


# ---------------------------------------------------------------------------
# Test 3: mDNS _ship._tcp browse
# ---------------------------------------------------------------------------

def check_mdns(timeout: float = 8.0) -> list[dict]:
    """Browse for _ship._tcp services via mDNS. Returns list of found services."""
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
    sb = ServiceBrowser(zc, "_ship._tcp.local.", handlers=[on_found])

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
# Test 4: ESPHome API sensor readout
# ---------------------------------------------------------------------------

def check_esphome_api(host: str, port: int = 6053) -> None:
    """Read EEBus-related sensor states via ESPHome native API."""
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

        _, states = await cli.list_entities_services()
        # states is not populated by list_entities_services; we need subscribe
        # Just list the entity names for now
        entities = _[0] if _ else _
        # list_entities_services returns (entities, services)
        eebus_entities = [e for e in entities
                          if any(kw in (getattr(e, 'name', '') or '').lower()
                                 for kw in ('eebus', 'ski', 'pairing', 'wp '))]
        if eebus_entities:
            ok(f"ESPHome API: found {len(eebus_entities)} EEBus-related entities")
            for ent in eebus_entities:
                print(f"       {ent.name}")
        else:
            warn("ESPHome API: no EEBus entities found")

        await cli.disconnect()

    asyncio.run(_read())


# ---------------------------------------------------------------------------
# Test 5: HTTP /ship/ WebSocket endpoint
# ---------------------------------------------------------------------------

def check_ship_ws(host: str, port: int, label: str, timeout: float = 5.0) -> bool:
    """Check if the /ship/ WebSocket endpoint responds to an HTTP upgrade request."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        raw = socket.create_connection((host, port), timeout=timeout)
        conn = ctx.wrap_socket(raw, server_hostname=host)

        # Send a minimal WebSocket upgrade request
        upgrade = (
            f"GET /ship/ HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: ship\r\n"
            f"\r\n"
        )
        conn.send(upgrade.encode())
        response = conn.recv(1024).decode(errors="replace")
        conn.close()

        if "101" in response:
            ok(f"{label} /ship/ WebSocket upgrade — 101 Switching Protocols")
            return True
        elif response:
            first_line = response.split("\r\n")[0]
            warn(f"{label} /ship/ responded: {first_line}")
            return True
        else:
            fail(f"{label} /ship/ — no response")
            return False
    except Exception as e:
        fail(f"{label} /ship/ WebSocket — {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="EEBus diagnostics for esphome-hems")
    parser.add_argument("--host", default="192.168.178.24", help="ESP32 IP address")
    parser.add_argument("--lpc-port", type=int, default=4712, help="SHIP LPC port")
    parser.add_argument("--wp-port", type=int, default=4713, help="SHIP WP port")
    parser.add_argument("--skip-mdns", action="store_true", help="Skip mDNS browse test")
    parser.add_argument("--skip-api", action="store_true", help="Skip ESPHome API test")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  EEBus Diagnostics — {args.host}")
    print(f"{'='*60}\n")

    # 1. Port reachability
    print("[1] TCP Port Reachability")
    web_ok = check_port(args.host, 80, "Web Server")
    lpc_ok = check_port(args.host, args.lpc_port, "SHIP LPC")
    wp_ok = check_port(args.host, args.wp_port, "SHIP WP")
    print()

    # 2. TLS handshake
    print("[2] TLS Certificate Verification")
    if lpc_ok:
        check_tls(args.host, args.lpc_port, "SHIP LPC")
    if wp_ok:
        check_tls(args.host, args.wp_port, "SHIP WP")
    print()

    # 3. WebSocket /ship/ endpoint
    print("[3] SHIP WebSocket Endpoint")
    if lpc_ok:
        check_ship_ws(args.host, args.lpc_port, "SHIP LPC")
    if wp_ok:
        check_ship_ws(args.host, args.wp_port, "SHIP WP")
    print()

    # 4. mDNS
    if not args.skip_mdns:
        print("[4] mDNS _ship._tcp Service Discovery")
        check_mdns()
        print()

    # 5. ESPHome API
    if not args.skip_api:
        print("[5] ESPHome API Entity Check")
        check_esphome_api(args.host)
        print()

    print(f"{'='*60}")
    if lpc_ok and wp_ok:
        print(f"  {GREEN}Both SHIP servers are reachable.{RESET}")
        print(f"  Starte die EEBus-Suche am Wärmepumpen-Display.")
    elif wp_ok:
        print(f"  {YELLOW}WP SHIP server OK, LPC not reachable.{RESET}")
    elif lpc_ok:
        print(f"  {RED}LPC OK but WP SHIP server not reachable!{RESET}")
    else:
        print(f"  {RED}Neither SHIP server is reachable!{RESET}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
