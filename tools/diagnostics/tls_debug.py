#!/usr/bin/env python3
"""Quick TLS debug test for SHIP ports."""
import ssl
import socket
import sys
import traceback

host = "192.168.178.24"
for port in [4712, 4713]:
    print(f"\n--- Testing {host}:{port} ---")
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Force TLS 1.2 — mbedTLS on ESP32 typically doesn't support TLS 1.3
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        
        s = socket.create_connection((host, port), 5)
        print(f"  TCP connected to {port}")
        
        c = ctx.wrap_socket(s, server_hostname=host)
        print(f"  TLS OK: {c.version()}, cipher: {c.cipher()}")
        
        der = c.getpeercert(binary_form=True)
        if der:
            print(f"  Server cert: {len(der)} bytes")
        else:
            print("  No server cert returned")
        c.close()
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
