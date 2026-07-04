#!/usr/bin/env python3
"""
fake_steuerbox.py — EEBus §14a Steuerbox pairing/LPC test.

Connects to esphome-hems eebus_lpc port (4712) as a fake EG (EnergyGuard /
Netz-Steuerbox), completes the full SHIP handshake, then sends properly
addressed SPINE messages to activate a §14a LPC power limit.

SPINE addressing:
  NodeManagement:   entity [0], feature 0  (both sides)
  HEMS CS entity:   entity [1]  (confirmed from openeebus discovery_reply.inc test fixture)
    feature 1 = DeviceDiagnosis CLIENT
    feature 2 = LoadControl SERVER   ← write target for limits
    feature 3 = DeviceConfiguration SERVER
    feature 4 = DeviceDiagnosis SERVER
  Our EG entity:    entity [1]  (matches real EG format from discovery_response.inc)
    feature 1 = DeviceDiagnosis CLIENT
    feature 2 = LoadControl CLIENT  ← binding clientAddress and LPC write src
    feature 3 = DeviceConfiguration CLIENT
    feature 4 = ElectricalConnection CLIENT
    feature 5 = DeviceDiagnosis SERVER

Requires:  pip install cryptography

Usage:
  # Manual pairing only
  python tools/fake_steuerbox.py 192.168.178.24

  # Full auto §14a scenario: announce UC, activate limit, revoke after 60 s
  python tools/fake_steuerbox.py 192.168.178.24 --scenario
  python tools/fake_steuerbox.py 192.168.178.24 --scenario --limit 6000 --hold 120

Before connecting (first-time pairing):
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
import time


# ── Certificate ───────────────────────────────────────────────────────────────

_CERT_CACHE = os.path.join(os.path.dirname(__file__), ".fake_steuerbox_cert.pem")
_KEY_CACHE  = os.path.join(os.path.dirname(__file__), ".fake_steuerbox_key.pem")


def load_or_generate_cert():
    """Return (cert_pem, key_pem, ski). Reuses a saved cert so the SKI is
    stable across runs — the HEMS only needs to accept pairing once."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        if os.path.exists(_CERT_CACHE) and os.path.exists(_KEY_CACHE):
            with open(_CERT_CACHE, "rb") as f:
                cert_pem = f.read()
            with open(_KEY_CACHE, "rb") as f:
                key_pem = f.read()
            key = serialization.load_pem_private_key(key_pem, password=None)
            raw_point = key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            ski = hashlib.sha1(raw_point).hexdigest()
            return cert_pem, key_pem, ski
    except Exception:
        pass
    cert_pem, key_pem, ski = generate_cert()
    try:
        with open(_CERT_CACHE, "wb") as f:
            f.write(cert_pem)
        with open(_KEY_CACHE, "wb") as f:
            f.write(key_pem)
    except Exception:
        pass
    return cert_pem, key_pem, ski


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


def get_peer_ski(sock) -> str:
    """Compute the HEMS SKI = SHA1(public-key uncompressed point) from the TLS peer cert."""
    try:
        from cryptography.hazmat.primitives import serialization
        import cryptography.x509
        peer_der = sock.getpeercert(binary_form=True)
        if not peer_der:
            return ""
        cert = cryptography.x509.load_der_x509_certificate(peer_der)
        raw_point = cert.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        ski = hashlib.sha1(raw_point).hexdigest()
        return ski
    except Exception as exc:
        print(f"  Warning: could not extract peer SKI from TLS cert: {exc}")
        return ""


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
    if opcode == 0x09:  # ping → pong
        ws_send(sock, payload)
        return ws_recv(sock)
    return payload


# ── SHIP protocol helpers ─────────────────────────────────────────────────────

MSG_CTRL = 0x01
MSG_DATA = 0x02

_spine_counter = 0


def _next_counter() -> int:
    global _spine_counter
    _spine_counter += 1
    return _spine_counter


def ship_send(sock, payload: dict):
    raw = bytes([MSG_CTRL]) + json.dumps(payload, separators=(",", ":")).encode()
    print(f"  -> CTRL {json.dumps(payload)}")
    ws_send(sock, raw)


def ship_recv(sock) -> dict:
    raw = ws_recv(sock)
    msg_type, body = raw[0], raw[1:]
    parsed = json.loads(body)
    tag = {MSG_CTRL: "CTRL", MSG_DATA: "DATA"}.get(msg_type, f"0x{msg_type:02x}")
    print(f"  <- [{tag}] {json.dumps(parsed)[:200]}")
    return parsed


# ── SHIP handshake ────────────────────────────────────────────────────────────

def ship_handshake(sock, local_ship_id: str) -> str:
    """Runs the full SHIP handshake. Returns the remote (HEMS) SHIP ID."""

    print("\n[1/5] CMI Init")
    ws_send(sock, bytes([0x00, 0x00]))
    init = ws_recv(sock)
    if init[:2] != bytes([0x00, 0x00]):
        raise ValueError(f"Unexpected CMI init response: {init.hex()}")
    print("      OK")

    print("\n[2/5] Hello")
    ship_send(sock, {"connectionHello": [{"phase": "ready"}, {"waiting": 60000}]})
    resp = ship_recv(sock)
    entries = resp.get("connectionHello", [])
    phase = next((e.get("phase") for e in entries if "phase" in e), None)
    if phase != "ready":
        raise ValueError(f"Hello: expected phase=ready, got {phase!r}")
    print("      OK")

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

    print("\n[4/5] PIN State")
    ship_send(sock, {"connectionPinState": [{"pinState": "none"}]})
    resp = ship_recv(sock)
    entries = resp.get("connectionPinState", [])
    pin = next((e.get("pinState") for e in entries if "pinState" in e), None)
    if pin not in ("none", "pinOk"):
        raise ValueError(f"PIN state: unexpected value {pin!r}")
    print("      OK")

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


# ── SPINE helpers ─────────────────────────────────────────────────────────────

def _spine_addr(device: str, entity: list, feature: int) -> list:
    """Build a SPINE FeatureAddressType as an EEBus SEQUENCE (array of single-key objects).

    openeebus's EebusDataSequenceFromJsonObjectItem() requires the JSON to be
    an array where each field is a separate single-key object element.
    A flat object {"device": ..., "entity": ..., "feature": ...} would fail the
    JsonIsArray() check and cause the entire datagram parse to be rejected.
    """
    return [{"device": device}, {"entity": entity}, {"feature": feature}]


def spine_send(
    sock,
    cmd_sequence: list,
    cmd_classifier: str,
    src_addr: str, src_entity: list, src_feature: int,
    dst_addr: str, dst_entity: list, dst_feature: int,
    label: str = "",
    ack_request: bool = False,
):
    """Send a properly-addressed SPINE datagram in a SHIP DATA frame.

    Wire format matches what a real EEBus device sends (confirmed against
    openeebus test fixtures limits_write.inc / use_case_reply.inc):

      - payload is a SEQUENCE (list): [{"cmd": [...]}]
      - cmd is a list; each cmd item is itself a SEQUENCE (list of single-key objects)
      - cmd_sequence is that inner list, e.g.:
          notify: [{"nodeManagementUseCaseData": [...]}]
          write:  [{"function": "..."}, {"filter": ...}, {"data": [...]}]

    SPINE device addresses ("d:_n:{vendor}_{serial}") are completely separate
    from TLS cert SKIs. HEMS address: "d:_n:DIY_HEMS-CS-01" (eebus_lpc.cpp:493-497).
    """
    header = [
        {"specificationVersion": "1.3.0"},
        {"addressSource":      _spine_addr(src_addr, src_entity, src_feature)},
        {"addressDestination": _spine_addr(dst_addr, dst_entity, dst_feature)},
        {"msgCounter":         _next_counter()},
        {"cmdClassifier":      cmd_classifier},
    ]
    if ack_request:
        header.append({"ackRequest": True})
    datagram = {
        "datagram": [{
            "header": header,
            "payload": [
                {"cmd": [cmd_sequence]},
            ],
        }]
    }
    msg = {
        "data": [
            {"header": [{"protocolId": "ee1.0"}]},
            {"payload": datagram},
        ]
    }
    raw = bytes([MSG_DATA]) + json.dumps(msg, separators=(",", ":")).encode()
    payload_json = json.dumps(cmd_sequence, separators=(",", ":"))
    print(
        f"  -> DATA [{label}] {cmd_classifier} "
        f"src={src_addr[:20]}[{','.join(str(e) for e in src_entity)}]:{src_feature} "
        f"-> dst=[{','.join(str(e) for e in dst_entity)}]:{dst_feature} "
        f"cmd={payload_json[:300]}"
    )
    ws_send(sock, raw)


def drain_incoming(sock, timeout: float = 1.0, our_addr=None, hems_addr=None):
    """Read and print queued incoming frames. Auto-replies to HEMS discovery read requests
    when our_addr and hems_addr are provided."""
    frames = []
    sock.settimeout(timeout)
    try:
        while True:
            raw = ws_recv(sock)
            msg_type = raw[0]
            tag = {MSG_CTRL: "CTRL", MSG_DATA: "DATA"}.get(msg_type, f"0x{msg_type:02x}")
            try:
                parsed = json.loads(raw[1:])
                frames.append((tag, parsed))
                print(f"  <- [{tag}] {json.dumps(parsed)[:2000]}")
                if msg_type == MSG_DATA and our_addr and hems_addr:
                    _handle_spine_read(sock, parsed, our_addr, hems_addr)
            except Exception:
                frames.append((tag, raw[1:]))
                print(f"  <- [{tag}] (raw) {raw[1:80]!r}")
    except (TimeoutError, OSError):
        pass
    finally:
        sock.settimeout(None)
    return frames


def send_result(
    sock,
    our_addr: str, hems_addr: str,
    src_entity: list, src_feature: int,
    dst_entity: list, dst_feature: int,
    msg_counter_ref: int,
    label: str = "Result",
):
    """Send RESULT (errorNumber=0) in response to a HEMS call."""
    datagram = {
        "datagram": [{
            "header": [
                {"specificationVersion": "1.3.0"},
                {"addressSource":      _spine_addr(our_addr, src_entity, src_feature)},
                {"addressDestination": _spine_addr(hems_addr, dst_entity, dst_feature)},
                {"msgCounter":         _next_counter()},
                {"msgCounterReference": msg_counter_ref},
                {"cmdClassifier":      "result"},
            ],
            "payload": [
                {"cmd": [[{"resultData": [{"errorNumber": 0}]}]]},
            ],
        }]
    }
    msg = {
        "data": [
            {"header": [{"protocolId": "ee1.0"}]},
            {"payload": datagram},
        ]
    }
    raw = bytes([MSG_DATA]) + json.dumps(msg, separators=(",", ":")).encode()
    print(f"  -> DATA [{label}] result msgCounterRef={msg_counter_ref}")
    ws_send(sock, raw)


def send_subscription_request(
    sock,
    our_addr: str, hems_addr: str,
    client_entity: list, client_feature: int,
    server_entity: list, server_feature: int,
    server_feature_type: str,
    label: str = "Subscription",
):
    """Send nodeManagementSubscriptionRequestCall.

    Always sent from [0]:0 to [0]:0 (NodeManagement level).
    clientAddress/serverAddress specify the actual feature being subscribed.
    """
    print(f"\n[Sub] Sending {server_feature_type} subscription ({label})...")
    spine_send(
        sock,
        cmd_sequence=[
            {"nodeManagementSubscriptionRequestCall": [
                {"subscriptionRequest": [
                    {"clientAddress": [
                        {"device": our_addr},
                        {"entity": client_entity},
                        {"feature": client_feature},
                    ]},
                    {"serverAddress": [
                        {"device": hems_addr},
                        {"entity": server_entity},
                        {"feature": server_feature},
                    ]},
                    {"serverFeatureType": server_feature_type},
                ]},
            ]},
        ],
        cmd_classifier="call",
        src_addr=our_addr, src_entity=[0], src_feature=0,
        dst_addr=hems_addr, dst_entity=[0], dst_feature=0,
        label=label,
        ack_request=True,
    )


def send_discovery_reply(sock, msg_counter_ref: int, our_addr: str, hems_addr: str):
    """Reply to HEMS nodeManagementDetailedDiscoveryData read with our full entity structure.

    Format matches the real EG format from discovery_response.inc test fixture:
    - device field present in all entityAddress and featureAddress entries
    - entity [1] features numbered 1-5 (not 0-4)
    - entity [1] type "GridGuard" (required by cs_lpc.c valid_entity_types[])
    """
    discovery_data = [
        {"specificationVersionList": [{"specificationVersion": ["1.3.0"]}]},
        {"deviceInformation": [{"description": [
            {"deviceAddress": [{"device": our_addr}]},
            {"deviceType": "ElectricitySupplySystem"},
            {"networkFeatureSet": "simple"},
        ]}]},
        {"entityInformation": [
            [{"description": [
                {"entityAddress": [{"device": our_addr}, {"entity": [0]}]},
                {"entityType": "DeviceInformation"},
            ]}],
            [{"description": [
                {"entityAddress": [{"device": our_addr}, {"entity": [1]}]},
                {"entityType": "GridGuard"},
            ]}],
        ]},
        {"featureInformation": [
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [0]}, {"feature": 0}]},
                {"featureType": "NodeManagement"},
                {"role": "special"},
                {"supportedFunction": [
                    [{"function": "nodeManagementUseCaseData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "nodeManagementDetailedDiscoveryData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "nodeManagementSubscriptionRequestCall"},
                     {"possibleOperations": []}],
                    [{"function": "nodeManagementBindingRequestCall"},
                     {"possibleOperations": []}],
                ]},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 1}]},
                {"featureType": "DeviceDiagnosis"},
                {"role": "client"},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 2}]},
                {"featureType": "LoadControl"},
                {"role": "client"},
                {"supportedFunction": [
                    [{"function": "loadControlLimitListData"},
                     {"possibleOperations": [{"read": []}, {"write": []}]}],
                ]},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 3}]},
                {"featureType": "DeviceConfiguration"},
                {"role": "client"},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 4}]},
                {"featureType": "ElectricalConnection"},
                {"role": "client"},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 5}]},
                {"featureType": "DeviceDiagnosis"},
                {"role": "server"},
            ]}],
        ]},
    ]

    datagram = {
        "datagram": [{
            "header": [
                {"specificationVersion": "1.3.0"},
                {"addressSource":      _spine_addr(our_addr, [0], 0)},
                {"addressDestination": _spine_addr(hems_addr, [0], 0)},
                {"msgCounter":         _next_counter()},
                {"msgCounterReference": msg_counter_ref},
                {"cmdClassifier":      "reply"},
            ],
            "payload": [
                {"cmd": [[{"nodeManagementDetailedDiscoveryData": discovery_data}]]},
            ],
        }]
    }
    msg = {
        "data": [
            {"header": [{"protocolId": "ee1.0"}]},
            {"payload": datagram},
        ]
    }
    raw = bytes([MSG_DATA]) + json.dumps(msg, separators=(",", ":")).encode()
    print(
        f"  -> DATA [DiscoveryReply] reply "
        f"src={our_addr[:20]}[0]:0 -> dst=[0]:0  (msgCounterRef={msg_counter_ref})"
    )
    ws_send(sock, raw)


def send_use_case_reply(sock, msg_counter_ref: int, our_addr: str, hems_addr: str):
    """Reply to HEMS nodeManagementUseCaseData READ with our EG/LPC use case support."""
    use_case_data = [
        {"useCaseInformation": [
            [
                {"address": [{"device": our_addr}, {"entity": [1]}]},
                {"actor": "EnergyGuard"},
                {"useCaseSupport": [
                    [
                        {"useCaseName": "limitationOfPowerConsumption"},
                        {"useCaseVersion": "1.0.0"},
                        {"useCaseAvailable": True},
                        {"scenarioSupport": [1, 2, 3, 4]},
                        {"useCaseDocumentSubRevision": "release"},
                    ],
                ]},
            ]
        ]},
    ]
    datagram = {
        "datagram": [{
            "header": [
                {"specificationVersion": "1.3.0"},
                {"addressSource":      _spine_addr(our_addr, [0], 0)},
                {"addressDestination": _spine_addr(hems_addr, [0], 0)},
                {"msgCounter":         _next_counter()},
                {"msgCounterReference": msg_counter_ref},
                {"cmdClassifier":      "reply"},
            ],
            "payload": [
                {"cmd": [[{"nodeManagementUseCaseData": use_case_data}]]},
            ],
        }]
    }
    msg = {
        "data": [
            {"header": [{"protocolId": "ee1.0"}]},
            {"payload": datagram},
        ]
    }
    raw = bytes([MSG_DATA]) + json.dumps(msg, separators=(",", ":")).encode()
    print(
        f"  -> DATA [UseCaseReply] reply "
        f"src={our_addr[:20]}[0]:0 -> dst=[0]:0  (msgCounterRef={msg_counter_ref})"
    )
    ws_send(sock, raw)


def _extract_datagram(parsed: dict):
    """Extract the first datagram dict from a SHIP DATA payload."""
    for item in parsed.get("data", []):
        if "payload" not in item:
            continue
        p = item["payload"]
        dg_list = None
        if isinstance(p, dict):
            dg_list = p.get("datagram")
        elif isinstance(p, list):
            for pi in p:
                if isinstance(pi, dict) and "datagram" in pi:
                    dg_list = pi["datagram"]
                    break
        if dg_list:
            return dg_list[0] if isinstance(dg_list, list) else dg_list
    return None


def _handle_spine_read(sock, parsed: dict, our_addr: str, hems_addr: str):
    """Auto-reply to HEMS SPINE requests (discovery READ, subscription calls, use case READ)."""
    try:
        datagram = _extract_datagram(parsed)
        if not datagram:
            return

        header = datagram.get("header", [])
        if isinstance(header, dict):
            header = [header]

        classifier = None
        msg_counter = None
        src_entity = [0]
        src_feature = 0

        for entry in header:
            if not isinstance(entry, dict):
                continue
            if "cmdClassifier" in entry:
                classifier = entry["cmdClassifier"]
            if "msgCounter" in entry:
                msg_counter = entry["msgCounter"]
            if "addressSource" in entry:
                addr = entry["addressSource"]
                if isinstance(addr, list):
                    for a in addr:
                        if isinstance(a, dict):
                            if "entity" in a:
                                src_entity = a["entity"]
                            if "feature" in a:
                                src_feature = a["feature"]

        if msg_counter is None:
            return

        raw_str = json.dumps(parsed)

        if classifier == "read" and "nodeManagementDetailedDiscoveryData" in raw_str:
            print(f"  [SPINE] Auto-reply: discovery REPLY (msgCounter={msg_counter})")
            send_discovery_reply(sock, msg_counter, our_addr, hems_addr)

        elif classifier == "call" and "nodeManagementSubscriptionRequestCall" in raw_str:
            print(f"  [SPINE] Auto-reply: RESULT for subscription (msgCounter={msg_counter})")
            send_result(
                sock, our_addr, hems_addr,
                src_entity=[0], src_feature=0,
                dst_entity=src_entity, dst_feature=src_feature,
                msg_counter_ref=msg_counter, label="SubscriptionResult",
            )

        elif classifier == "read" and "nodeManagementUseCaseData" in raw_str:
            print(f"  [SPINE] Auto-reply: use case REPLY (msgCounter={msg_counter})")
            send_use_case_reply(sock, msg_counter, our_addr, hems_addr)

        elif classifier == "read" and "deviceDiagnosisHeartbeatData" in raw_str:
            print(f"  [SPINE] Auto-reply: heartbeat REPLY (msgCounter={msg_counter})")
            _heartbeat_counter = getattr(_handle_spine_read, "_hb_counter", 0) + 1
            _handle_spine_read._hb_counter = _heartbeat_counter
            hb_data = {
                "datagram": [{
                    "header": [
                        {"specificationVersion": "1.3.0"},
                        {"addressSource":      _spine_addr(our_addr, [1], 5)},
                        {"addressDestination": _spine_addr(hems_addr, [1], 1)},
                        {"msgCounter":         _next_counter()},
                        {"msgCounterReference": msg_counter},
                        {"cmdClassifier":      "reply"},
                    ],
                    "payload": [{"cmd": [[{"deviceDiagnosisHeartbeatData": [
                        {"heartbeatCounter": _heartbeat_counter},
                        {"heartbeatTimeout": 60},
                    ]}]]}],
                }]
            }
            hb_msg = {
                "data": [
                    {"header": [{"protocolId": "ee1.0"}]},
                    {"payload": hb_data},
                ]
            }
            raw_hb = bytes([MSG_DATA]) + json.dumps(hb_msg, separators=(",", ":")).encode()
            print(f"  -> DATA [HeartbeatReply] reply src={our_addr[:20]}[1]:5 -> dst=[1]:1")
            ws_send(sock, raw_hb)

        elif classifier == "result":
            ref = next((e.get("msgCounterReference") for e in header if "msgCounterReference" in e), None)
            print(f"  [SPINE] Received RESULT (msgCounterRef={ref})")

    except Exception as exc:
        print(f"  [SPINE] Warning in auto-reply handler: {exc}")


def send_use_case_announce(sock, our_addr: str, hems_addr: str):
    """Send NodeManagementUseCaseData notify: announce LPC as EG (EnergyGuard).

    Wire format matches use_case_reply.inc test fixture — all nested structures
    use EEBUS SEQUENCE encoding (arrays of single-key objects).
    NodeManagement is always at entity [0], feature 0 on both sides.
    """
    print("\n[UC] Announcing EG/LPC use case via NodeManagementUseCaseData")
    spine_send(
        sock,
        cmd_sequence=[
            {"nodeManagementUseCaseData": [
                {"useCaseInformation": [
                    # Each useCaseInformation entry is a SEQUENCE
                    [
                        {"address": [{"device": our_addr}, {"entity": [1]}]},
                        {"actor": "EnergyGuard"},
                        {"useCaseSupport": [
                            # Each useCaseSupport entry is a SEQUENCE
                            [
                                {"useCaseName": "limitationOfPowerConsumption"},
                                {"useCaseVersion": "1.0.0"},
                                {"useCaseAvailable": True},
                                {"scenarioSupport": [1, 2, 3, 4]},
                            ]
                        ]},
                    ]
                ]},
            ]},
        ],
        cmd_classifier="notify",
        src_addr=our_addr, src_entity=[0], src_feature=0,
        dst_addr=hems_addr, dst_entity=[0], dst_feature=0,
        label="NodeManagementUseCaseData",
    )


def send_binding_request(sock, our_addr: str, hems_addr: str):
    """Send nodeManagementBindingRequestCall to establish LoadControl binding.

    SPINE device_local.c checks BINDING_MANAGER_HAS_BINDING() before accepting
    any write command. Without a prior binding, writes silently return
    kEebusErrorNoChange. This must be sent before loadControlLimitListData writes.

    Sent FROM our entity [0]:0 TO HEMS entity [0]:0 (NodeManagement on both sides).
    clientAddress = our LoadControl CLIENT (entity [1], feature 2 per our discovery reply).
    serverAddress = HEMS LoadControl SERVER (entity [1], feature 2 per discovery_reply.inc).
    """
    print("\n[Bind] Sending LoadControl binding request (nodeManagementBindingRequestCall)...")
    spine_send(
        sock,
        cmd_sequence=[
            {"nodeManagementBindingRequestCall": [
                {"bindingRequest": [
                    {"clientAddress": [
                        {"device": our_addr},
                        {"entity": [1]},
                        {"feature": 2},
                    ]},
                    {"serverAddress": [
                        {"device": hems_addr},
                        {"entity": [1]},
                        {"feature": 2},
                    ]},
                    {"serverFeatureType": "LoadControl"},
                ]},
            ]},
        ],
        cmd_classifier="call",
        src_addr=our_addr, src_entity=[0], src_feature=0,
        dst_addr=hems_addr, dst_entity=[0], dst_feature=0,
        label="BindingRequest",
        ack_request=True,
    )


def send_lpc_limit(sock, limit_w: float, active: bool, our_addr: str, hems_addr: str):
    """Write a §14a LPC power limit to the HEMS LoadControl server feature.

    Wire format matches limits_write.inc test fixture — cmd SEQUENCE includes
    function identifier, partial-update filter, and all data in SEQUENCE encoding.
    Target: HEMS CS entity [1], LoadControl SERVER = feature 2.
    Source: our EG entity [1], LoadControl CLIENT = feature 2.
    """
    action = "ACTIVATE" if active else "REVOKE"
    print(f"\n[§14a] {action} LPC limit: {limit_w:.0f} W  isLimitActive={active}")
    spine_send(
        sock,
        cmd_sequence=[
            {"function": "loadControlLimitListData"},
            {"filter": [[{"cmdControl": [{"partial": []}]}]]},
            {"loadControlLimitListData": [
                {"loadControlLimitData": [
                    [
                        {"limitId": 0},
                        {"isLimitActive": active},
                        {"timePeriod": [{"endTime": "PT1H"}]},
                        {"value": [{"number": int(limit_w)}, {"scale": 0}]},
                    ]
                ]},
            ]},
        ],
        cmd_classifier="write",
        src_addr=our_addr, src_entity=[1], src_feature=2,   # our EG entity, LoadControl CLIENT
        dst_addr=hems_addr, dst_entity=[1], dst_feature=2,  # HEMS CS entity, LoadControl SERVER
        label=f"LoadControlLimit {action}",
        ack_request=True,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="EEBus fake Steuerbox (§14a EG) — pairing, use-case announce, LPC test"
    )
    ap.add_argument("host", help="HEMS IP address, e.g. 192.168.178.24")
    ap.add_argument("--port", type=int, default=4712, help="eebus_lpc SHIP port (default 4712)")
    ap.add_argument(
        "--limit", type=float, default=4200.0,
        help="LPC power limit in W (default 4200)",
    )
    ap.add_argument(
        "--hold", type=int, default=60,
        help="Seconds to hold the §14a limit before revoking in --scenario mode (default 60)",
    )
    ap.add_argument(
        "--scenario", action="store_true",
        help=(
            "Full §14a scenario: announce use case, activate limit, wait --hold seconds, "
            "revoke, then disconnect. No manual prompts."
        ),
    )
    ap.add_argument(
        "--send-limit", action="store_true",
        help="After pairing: send the LPC limit once (manual mode, no auto-revoke)",
    )
    ap.add_argument(
        "--no-pause", action="store_true",
        help="Skip 'press Enter' prompts",
    )
    args = ap.parse_args()

    print("=" * 60)
    print("EEBus Fake Steuerbox (§14a EG / EnergyGuard)")
    print("=" * 60)

    print("\nLoading/generating TLS certificate (EC P-256)...")
    cert_pem, key_pem, our_ski = load_or_generate_cert()
    print(f"Fake Steuerbox SKI: {our_ski} (persistent — same each run)")

    if args.scenario:
        # Automatically trigger pairing mode on the HEMS via HTTP API
        try:
            import urllib.request
            url = f"http://{args.host}/button/esphome_hems_cs_pairing-modus_starten/press"
            req = urllib.request.Request(url, data=b"", method="POST")
            req.add_header("Content-Length", "0")
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
            print(f"[Pairing] Triggered pairing mode on HEMS ({url})")
        except Exception as e:
            print(f"[Pairing] Warning: could not trigger pairing mode: {e}")
            print(f"[Pairing] Manually press 'CS Pairing-Modus starten' on http://{args.host}")
    else:
        print(f"""
Prerequisites (first-time pairing):
  1. Open http://{args.host} in a browser
  2. Press "CS Pairing-Modus starten"
  3. Continue — script will connect and complete SHIP handshake
  4. Press "CS Pairing akzeptieren" in HEMS web UI (optional)
""")
        if not args.no_pause:
            input("Press Enter once pairing mode is active on the HEMS...")

    cert_file = key_file = None
    sock = None
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

        print(f"\nConnecting to {args.host}:{args.port}...")
        raw = socket.create_connection((args.host, args.port), timeout=15)
        sock = ctx.wrap_socket(raw, server_hostname=args.host)
        print(f"TLS established  ({sock.version()})")

        # Extract HEMS SKI from the TLS peer certificate (used for SHIP handshake display only)
        hems_ski = get_peer_ski(sock)
        if hems_ski:
            print(f"HEMS SKI (from TLS cert): {hems_ski}")
        else:
            print("WARNING: Could not extract HEMS SKI from TLS cert — SHIP display incomplete")

        # SPINE device addresses — completely independent of TLS cert SKI.
        # HEMS address is "d:_n:{vendor}_{serial}" (eebus_device_info.c:86).
        # vendor="DIY", serial="HEMS-CS-01" come from eebus_lpc.cpp:493-497.
        # GetFeatureWithAddress() drops any message where dst.device != device->address.
        our_addr  = f"d:_n:FakeSteuerbox_{our_ski[:8]}"
        hems_addr = "d:_n:DIY_HEMS-CS-01"
        print(f"SPINE our  addr : {our_addr}")
        print(f"SPINE HEMS addr : {hems_addr}")

        ws_upgrade(sock, args.host, args.port)
        print("WebSocket upgraded  (/ship/  subprotocol=ship)")

        local_id = f"FakeSteuerbox-{our_ski[:8]}"
        server_id = ship_handshake(sock, local_id)

        print(f"""
+----------------------------------------------------------+
|  PAIRING COMPLETE — kDataExchange (state 38) reached!   |
|  Our SKI  : {our_ski[:48]:<48} |
|  HEMS SKI : {hems_ski[:48]:<48} |
|  Server ID: {server_id:<48} |
+----------------------------------------------------------+
""")

        # Step 2: HEMS sends discovery READ immediately after DataExchange.
        # drain_incoming auto-replies with our discovery REPLY.
        print("\n[SPINE] Draining HEMS discovery + auto-replying...")
        drain_incoming(sock, timeout=2.0, our_addr=our_addr, hems_addr=hems_addr)
        time.sleep(0.5)

        # Step 3: Subscribe our NM to HEMS NM (EG→CS, NM[0]:0→NM[0]:0).
        # After this the HEMS will: subscribe back to our NM, then READ our use cases.
        send_subscription_request(
            sock, our_addr, hems_addr,
            client_entity=[0], client_feature=0,
            server_entity=[0], server_feature=0,
            server_feature_type="NodeManagement",
            label="NMSubscription",
        )
        time.sleep(0.5)

        # Drain: RESULT for our NM sub + HEMS subscribes to our NM (auto-RESULT)
        #        + HEMS reads our use cases (auto-REPLY)
        drain_incoming(sock, timeout=2.5, our_addr=our_addr, hems_addr=hems_addr)
        time.sleep(0.5)

        if args.scenario:
            # ── Full §14a scenario ────────────────────────────────────────

            # Step 8: Subscribe our LC CLIENT to HEMS LC SERVER.
            send_subscription_request(
                sock, our_addr, hems_addr,
                client_entity=[1], client_feature=2,
                server_entity=[1], server_feature=2,
                server_feature_type="LoadControl",
                label="LCSubscription",
            )
            time.sleep(0.5)
            drain_incoming(sock, timeout=1.0, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.5)

            # Step 9: Establish LoadControl binding before any write.
            send_binding_request(sock, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.5)
            drain_incoming(sock, timeout=1.5, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.5)

            send_lpc_limit(sock, args.limit, active=True, our_addr=our_addr, hems_addr=hems_addr)

            # Drain any RESULT/ack frames
            time.sleep(0.5)
            drain_incoming(sock, timeout=0.5, our_addr=our_addr, hems_addr=hems_addr)

            print(f"\n[§14a] Limit active for {args.hold} s. Check HEMS log for 'EEBUS LPC limit active'.")
            step = 5
            for remaining in range(args.hold, 0, -step):
                print(f"  ... {remaining:3d} s remaining")
                time.sleep(min(step, remaining))
                # Drain any unsolicited frames to keep the connection alive
                drain_incoming(sock, timeout=0.1, our_addr=our_addr, hems_addr=hems_addr)

            send_lpc_limit(sock, args.limit, active=False, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.5)
            drain_incoming(sock, timeout=0.5, our_addr=our_addr, hems_addr=hems_addr)
            print("\n[§14a] Limit revoked. Check HEMS log for 'EEBUS LPC limit cleared'.")
            time.sleep(1.0)
            print("\n[Scenario] Complete — disconnecting.")

        elif args.send_limit:
            # ── Manual one-shot limit ─────────────────────────────────────
            if not args.no_pause:
                input("\nPress Enter to send subscriptions + binding + LPC limit...")
            send_subscription_request(
                sock, our_addr, hems_addr,
                client_entity=[1], client_feature=2,
                server_entity=[1], server_feature=2,
                server_feature_type="LoadControl",
                label="LCSubscription",
            )
            time.sleep(0.5)
            drain_incoming(sock, timeout=1.0, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.3)
            send_binding_request(sock, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.5)
            drain_incoming(sock, timeout=1.0, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.3)
            send_lpc_limit(sock, args.limit, active=True, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.5)
            drain_incoming(sock, timeout=0.5, our_addr=our_addr, hems_addr=hems_addr)
            print(f"\nLPC limit sent: {args.limit:.0f} W")
            print("Check HEMS log for 'EEBUS LPC limit active'")
            print("\nConnection open. Press Enter to disconnect (this revokes the limit).")
            if not args.no_pause:
                input()
            send_lpc_limit(sock, args.limit, active=False, our_addr=our_addr, hems_addr=hems_addr)
            time.sleep(0.5)
            drain_incoming(sock, timeout=0.5, our_addr=our_addr, hems_addr=hems_addr)

        else:
            # ── Pairing-only mode ─────────────────────────────────────────
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
            if f:
                try:
                    os.unlink(f)
                except Exception:
                    pass


if __name__ == "__main__":
    main()
