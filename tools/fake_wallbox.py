#!/usr/bin/env python3
"""
fake_wallbox.py — EEBus controllable-device (CS/LPC) simulator for EG2 testing.

Connects to the esphome-hems eebus_eg2 port (4714) as a fake wallbox (EVSE)
and plays the ControllableSystem side of the LPC use case:

  - replies to detailed discovery READ with an EVSE entity
  - announces CS/LPC + CS/LPP use case support
  - ACKs NodeManagement subscription/binding calls
  - serves LoadControl limit descriptions + limit data
  - serves DeviceConfiguration failsafe key descriptions + data
  - accepts loadControlLimitListData WRITEs  ← the actual §14a limit
  - accepts deviceConfigurationKeyValueListData WRITEs (failsafe setup)
  - answers heartbeat READs and sends periodic heartbeat NOTIFYs

Every received LPC limit WRITE is printed prominently, so running
fake_steuerbox.py --scenario against the CS port (4712) in parallel verifies
the full chain:  Steuerbox → HEMS CS → EG2 → (this) wallbox.

Message formats are derived from the openeebus EG/LPC test fixtures
(openeebus/tests/src/use_case/actor/eg/lpc/).

Usage:
  python tools/fake_wallbox.py 192.168.178.24                 # pair + serve 300 s
  python tools/fake_wallbox.py 192.168.178.24 --duration 600
"""

import argparse
import json
import os
import socket
import ssl
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_steuerbox as fs

# Separate persistent identity from the fake Steuerbox
fs._CERT_CACHE = os.path.join(os.path.dirname(__file__), ".fake_wallbox_cert.pem")
fs._KEY_CACHE  = os.path.join(os.path.dirname(__file__), ".fake_wallbox_key.pem")


# ── State ─────────────────────────────────────────────────────────────────────

class WallboxState:
    def __init__(self):
        # limitId 0 = consume (LPC), limitId 1 = produce (LPP)
        self.limits = {
            0: {"active": False, "value": 11000},
            1: {"active": False, "value": 10000},
        }
        self.failsafe_limit_w = 4200
        self.failsafe_duration = "PT2H"
        self.heartbeat_counter = 0
        # subscriber client feature addresses by serverFeatureType
        self.subscribers = {}
        self.received_writes = []


# ── Datagram helpers ──────────────────────────────────────────────────────────

def _hdr_get(header: list, key: str):
    for item in header:
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def _addr_parts(addr: list):
    """Split a SPINE feature address SEQUENCE into (device, entity, feature)."""
    dev, ent, feat = None, None, None
    for item in addr or []:
        if "device" in item:  dev  = item["device"]
        if "entity" in item:  ent  = item["entity"]
        if "feature" in item: feat = item["feature"]
    return dev, ent, feat


def _get_cmd_items(dg: dict):
    """Return the first cmd SEQUENCE (list of single-key dicts) of a datagram."""
    for entry in dg.get("payload", []):
        if "cmd" in entry:
            cmds = entry["cmd"]
            if cmds:
                return cmds[0]
    return []


def _cmd_function_names(cmd_items: list):
    names = []
    for item in cmd_items:
        for k in item:
            if k not in ("function", "filter"):
                names.append(k)
    return names


def send_reply(sock, our_addr, dg, cmd_sequence, label):
    """Send a REPLY: src = incoming destination feature, dst = incoming source."""
    header = dg.get("header", [])
    src = _hdr_get(header, "addressSource")
    dst = _hdr_get(header, "addressDestination")
    msg_counter = _hdr_get(header, "msgCounter")
    s_dev, s_ent, s_feat = _addr_parts(src)
    d_dev, d_ent, d_feat = _addr_parts(dst)
    datagram = {
        "datagram": [{
            "header": [
                {"specificationVersion": "1.3.0"},
                {"addressSource":      fs._spine_addr(our_addr, d_ent or [0], d_feat or 0)},
                {"addressDestination": fs._spine_addr(s_dev, s_ent or [0], s_feat or 0)},
                {"msgCounter":         fs._next_counter()},
                {"msgCounterReference": msg_counter},
                {"cmdClassifier":      "reply"},
            ],
            "payload": [{"cmd": [cmd_sequence]}],
        }]
    }
    msg = {"data": [{"header": [{"protocolId": "ee1.0"}]}, {"payload": datagram}]}
    raw = bytes([fs.MSG_DATA]) + json.dumps(msg, separators=(",", ":")).encode()
    print(f"  -> [{label}] reply (msgCounterRef={msg_counter})")
    fs.ws_send(sock, raw)


def send_result0(sock, our_addr, dg, label):
    header = dg.get("header", [])
    src = _hdr_get(header, "addressSource")
    dst = _hdr_get(header, "addressDestination")
    msg_counter = _hdr_get(header, "msgCounter")
    s_dev, s_ent, s_feat = _addr_parts(src)
    _, d_ent, d_feat = _addr_parts(dst)
    fs.send_result(
        sock,
        our_addr=our_addr, hems_addr=s_dev,
        src_entity=d_ent or [0], src_feature=d_feat or 0,
        dst_entity=s_ent or [0], dst_feature=s_feat or 0,
        msg_counter_ref=msg_counter, label=label,
    )


# ── Data builders (fixture-derived) ───────────────────────────────────────────

def discovery_data(our_addr):
    return [
        {"specificationVersionList": [{"specificationVersion": ["1.3.0"]}]},
        {"deviceInformation": [{"description": [
            {"deviceAddress": [{"device": our_addr}]},
            {"deviceType": "ChargingStation"},
            {"networkFeatureSet": "smart"},
        ]}]},
        {"entityInformation": [
            [{"description": [
                {"entityAddress": [{"device": our_addr}, {"entity": [0]}]},
                {"entityType": "DeviceInformation"},
            ]}],
            [{"description": [
                {"entityAddress": [{"device": our_addr}, {"entity": [1]}]},
                {"entityType": "EVSE"},
            ]}],
        ]},
        {"featureInformation": [
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [0]}, {"feature": 0}]},
                {"featureType": "NodeManagement"},
                {"role": "special"},
                {"supportedFunction": [
                    [{"function": "nodeManagementDetailedDiscoveryData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "nodeManagementUseCaseData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "nodeManagementSubscriptionData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "nodeManagementSubscriptionRequestCall"},
                     {"possibleOperations": []}],
                    [{"function": "nodeManagementSubscriptionDeleteCall"},
                     {"possibleOperations": []}],
                    [{"function": "nodeManagementBindingData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "nodeManagementBindingRequestCall"},
                     {"possibleOperations": []}],
                    [{"function": "nodeManagementBindingDeleteCall"},
                     {"possibleOperations": []}],
                    [{"function": "nodeManagementDestinationListData"},
                     {"possibleOperations": [{"read": []}]}],
                ]},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [0]}, {"feature": 1}]},
                {"featureType": "DeviceClassification"},
                {"role": "server"},
                {"supportedFunction": [
                    [{"function": "deviceClassificationManufacturerData"},
                     {"possibleOperations": [{"read": []}]}],
                ]},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 1}]},
                {"featureType": "LoadControl"},
                {"role": "server"},
                {"supportedFunction": [
                    [{"function": "loadControlLimitDescriptionListData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "loadControlLimitListData"},
                     {"possibleOperations": [{"read": []}, {"write": [{"partial": []}]}]}],
                ]},
                {"description": "LoadControl Server"},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 2}]},
                {"featureType": "DeviceConfiguration"},
                {"role": "server"},
                {"supportedFunction": [
                    [{"function": "deviceConfigurationKeyValueDescriptionListData"},
                     {"possibleOperations": [{"read": []}]}],
                    [{"function": "deviceConfigurationKeyValueListData"},
                     {"possibleOperations": [{"read": []}, {"write": [{"partial": []}]}]}],
                ]},
                {"description": "DeviceConfiguration Server"},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 3}]},
                {"featureType": "DeviceDiagnosis"},
                {"role": "server"},
                {"supportedFunction": [
                    [{"function": "deviceDiagnosisHeartbeatData"},
                     {"possibleOperations": [{"read": []}]}],
                ]},
                {"description": "DeviceDiagnosis Server"},
            ]}],
            [{"description": [
                {"featureAddress": [{"device": our_addr}, {"entity": [1]}, {"feature": 4}]},
                {"featureType": "ElectricalConnection"},
                {"role": "server"},
                {"supportedFunction": [
                    [{"function": "electricalConnectionCharacteristicListData"},
                     {"possibleOperations": [{"read": []}]}],
                ]},
                {"description": "ElectricalConnection Server"},
            ]}],
        ]},
    ]


def use_case_data(our_addr):
    def uc(name):
        return [
            {"useCaseName": name},
            {"useCaseVersion": "1.0.0"},
            {"useCaseAvailable": True},
            {"scenarioSupport": [1, 2, 3, 4]},
            {"useCaseDocumentSubRevision": "release"},
        ]
    return [
        {"useCaseInformation": [
            [
                {"address": [{"device": our_addr}, {"entity": [1]}]},
                {"actor": "ControllableSystem"},
                {"useCaseSupport": [
                    uc("limitationOfPowerConsumption"),
                    uc("limitationOfPowerProduction"),
                ]},
            ]
        ]},
    ]


def limit_description_data():
    return [
        {"loadControlLimitDescriptionData": [
            [
                {"limitId": 0},
                {"limitType": "signDependentAbsValueLimit"},
                {"limitCategory": "obligation"},
                {"limitDirection": "consume"},
                {"measurementId": 0},
                {"unit": "W"},
                {"scopeType": "activePowerLimit"},
            ],
            [
                {"limitId": 1},
                {"limitType": "signDependentAbsValueLimit"},
                {"limitCategory": "obligation"},
                {"limitDirection": "produce"},
                {"measurementId": 0},
                {"unit": "W"},
                {"scopeType": "activePowerLimit"},
            ],
        ]},
    ]


def limit_list_data(state: WallboxState):
    return [
        {"loadControlLimitData": [
            [
                {"limitId": 0},
                {"isLimitChangeable": True},
                {"isLimitActive": state.limits[0]["active"]},
                {"value": [{"number": state.limits[0]["value"]}, {"scale": 0}]},
            ],
            [
                {"limitId": 1},
                {"isLimitChangeable": True},
                {"isLimitActive": state.limits[1]["active"]},
                {"value": [{"number": state.limits[1]["value"]}, {"scale": 0}]},
            ],
        ]},
    ]


def config_description_data():
    return [
        {"deviceConfigurationKeyValueDescriptionData": [
            [
                {"keyId": 1},
                {"keyName": "failsafeConsumptionActivePowerLimit"},
                {"valueType": "scaledNumber"},
                {"unit": "W"},
            ],
            [
                {"keyId": 2},
                {"keyName": "failsafeDurationMinimum"},
                {"valueType": "duration"},
            ],
        ]},
    ]


def config_value_data(state: WallboxState):
    return [
        {"deviceConfigurationKeyValueData": [
            [
                {"keyId": 1},
                {"value": [{"scaledNumber": [{"number": state.failsafe_limit_w}, {"scale": 0}]}]},
                {"isValueChangeable": True},
            ],
            [
                {"keyId": 2},
                {"value": [{"duration": state.failsafe_duration}]},
                {"isValueChangeable": True},
            ],
        ]},
    ]


def heartbeat_data(state: WallboxState):
    state.heartbeat_counter += 1
    return [
        {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        {"heartbeatCounter": state.heartbeat_counter},
        {"heartbeatTimeout": "PT60S"},
    ]


def send_heartbeat_notify(sock, our_addr, state: WallboxState):
    sub = state.subscribers.get("DeviceDiagnosis")
    if not sub:
        return
    dev, ent, feat = sub
    fs.spine_send(
        sock,
        cmd_sequence=[{"deviceDiagnosisHeartbeatData": heartbeat_data(state)}],
        cmd_classifier="notify",
        src_addr=our_addr, src_entity=[1], src_feature=3,
        dst_addr=dev, dst_entity=ent, dst_feature=feat,
        label="HeartbeatNotify",
    )


def send_limit_notify(sock, our_addr, state: WallboxState):
    sub = state.subscribers.get("LoadControl")
    if not sub:
        print("  (no LoadControl subscriber — skipping limit NOTIFY)")
        return
    dev, ent, feat = sub
    fs.spine_send(
        sock,
        cmd_sequence=[
            {"function": "loadControlLimitListData"},
            {"filter": [[{"cmdControl": [{"partial": []}]}]]},
            {"loadControlLimitListData": limit_list_data(state)},
        ],
        cmd_classifier="notify",
        src_addr=our_addr, src_entity=[1], src_feature=1,
        dst_addr=dev, dst_entity=ent, dst_feature=feat,
        label="LimitNotify",
    )


# ── Write handlers ────────────────────────────────────────────────────────────

def handle_limit_write(sock, our_addr, dg, cmd_items, state: WallboxState):
    """Parse a loadControlLimitListData WRITE, apply it, ACK + NOTIFY."""
    for item in cmd_items:
        if "loadControlLimitListData" not in item:
            continue
        for part in item["loadControlLimitListData"]:
            for row in part.get("loadControlLimitData", []):
                limit_id, active, number, scale = None, None, None, 0
                for f in row:
                    if "limitId" in f:       limit_id = f["limitId"]
                    if "isLimitActive" in f: active = f["isLimitActive"]
                    if "value" in f:
                        for v in f["value"]:
                            if "number" in v: number = v["number"]
                            if "scale" in v:  scale = v["scale"]
                if limit_id is None:
                    continue
                watts = (number or 0) * (10 ** scale) if number is not None else None
                if limit_id in state.limits:
                    if active is not None:
                        state.limits[limit_id]["active"] = active
                    if number is not None:
                        state.limits[limit_id]["value"] = number
                state.received_writes.append((time.strftime("%H:%M:%S"), limit_id, watts, active))
                print(f"\n*** LPC LIMIT WRITE RECEIVED: limitId={limit_id} "
                      f"value={watts} W isLimitActive={active} ***\n")
    send_result0(sock, our_addr, dg, "LimitWriteAck")
    send_limit_notify(sock, our_addr, state)


def handle_config_write(sock, our_addr, dg, cmd_items, state: WallboxState):
    for item in cmd_items:
        if "deviceConfigurationKeyValueListData" not in item:
            continue
        for part in item["deviceConfigurationKeyValueListData"]:
            for row in part.get("deviceConfigurationKeyValueData", []):
                key_id, val = None, None
                for f in row:
                    if "keyId" in f: key_id = f["keyId"]
                    if "value" in f: val = f["value"]
                print(f"  Failsafe config write: keyId={key_id} value={json.dumps(val)}")
                if key_id == 1 and val:
                    for v in val:
                        if "scaledNumber" in v:
                            num = sc = 0
                            for x in v["scaledNumber"]:
                                if "number" in x: num = x["number"]
                                if "scale" in x:  sc = x["scale"]
                            state.failsafe_limit_w = num * (10 ** sc)
                elif key_id == 2 and val:
                    for v in val:
                        if "duration" in v:
                            state.failsafe_duration = v["duration"]
    send_result0(sock, our_addr, dg, "ConfigWriteAck")


def record_subscription(cmd_items, state: WallboxState, kind: str):
    """Record clientAddress by serverFeatureType from a subscription/binding call."""
    for item in cmd_items:
        req_list = item.get("nodeManagementSubscriptionRequestCall") or \
                   item.get("nodeManagementBindingRequestCall")
        if not req_list:
            continue
        for entry in req_list:
            req = entry.get("subscriptionRequest") or entry.get("bindingRequest")
            if not req:
                continue
            client, ftype = None, None
            for f in req:
                if "clientAddress" in f:     client = _addr_parts(f["clientAddress"])
                if "serverFeatureType" in f: ftype = f["serverFeatureType"]
            if client and ftype and kind == "subscription":
                state.subscribers[ftype] = client
                print(f"  {ftype} subscriber registered: dev={client[0]} "
                      f"entity={client[1]} feature={client[2]}")


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _merged_datagram(parsed: dict):
    """Extract the datagram and merge its SEQUENCE elements into one dict.

    SPINE encodes the datagram as a SEQUENCE: [{"header": [...]}, {"payload": [...]}].
    fs._extract_datagram returns only the first element, so merge all of them.
    """
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
            if isinstance(dg_list, dict):
                return dg_list
            merged = {}
            for element in dg_list:
                if isinstance(element, dict):
                    merged.update(element)
            return merged
    return None


def handle_datagram(sock, our_addr, parsed, state: WallboxState):
    dg = _merged_datagram(parsed)
    if not dg:
        return
    header = dg.get("header", [])
    classifier = _hdr_get(header, "cmdClassifier")
    cmd_items = _get_cmd_items(dg)
    fns = _cmd_function_names(cmd_items)
    fn = fns[0] if fns else "?"

    if classifier == "read":
        if fn == "nodeManagementDetailedDiscoveryData":
            send_reply(sock, our_addr, dg,
                       [{"nodeManagementDetailedDiscoveryData": discovery_data(our_addr)}],
                       "DiscoveryReply")
        elif fn == "nodeManagementUseCaseData":
            send_reply(sock, our_addr, dg,
                       [{"nodeManagementUseCaseData": use_case_data(our_addr)}],
                       "UseCaseReply")
        elif fn == "loadControlLimitDescriptionListData":
            send_reply(sock, our_addr, dg,
                       [{"loadControlLimitDescriptionListData": limit_description_data()}],
                       "LimitDescriptionReply")
        elif fn == "loadControlLimitListData":
            send_reply(sock, our_addr, dg,
                       [{"loadControlLimitListData": limit_list_data(state)}],
                       "LimitListReply")
        elif fn == "deviceConfigurationKeyValueDescriptionListData":
            send_reply(sock, our_addr, dg,
                       [{"deviceConfigurationKeyValueDescriptionListData": config_description_data()}],
                       "ConfigDescriptionReply")
        elif fn == "deviceConfigurationKeyValueListData":
            send_reply(sock, our_addr, dg,
                       [{"deviceConfigurationKeyValueListData": config_value_data(state)}],
                       "ConfigValueReply")
        elif fn == "deviceDiagnosisHeartbeatData":
            send_reply(sock, our_addr, dg,
                       [{"deviceDiagnosisHeartbeatData": heartbeat_data(state)}],
                       "HeartbeatReply")
        elif fn == "deviceClassificationManufacturerData":
            send_reply(sock, our_addr, dg,
                       [{"deviceClassificationManufacturerData": [
                           {"deviceName": "FakeWallbox"},
                           {"brandName": "DIY"},
                           {"vendorName": "DIY"},
                       ]}],
                       "ManufacturerReply")
        elif fn == "electricalConnectionCharacteristicListData":
            send_reply(sock, our_addr, dg,
                       [{"electricalConnectionCharacteristicListData": [
                           {"electricalConnectionCharacteristicData": [[
                               {"electricalConnectionId": 0},
                               {"parameterId": 0},
                               {"characteristicId": 0},
                               {"characteristicContext": "entity"},
                               {"characteristicType": "contractualConsumptionNominalMax"},
                               {"value": [{"number": 11000}, {"scale": 0}]},
                               {"unit": "W"},
                           ]]},
                       ]}],
                       "ElectricalConnReply")
        else:
            print(f"  (unhandled READ: {fn})")

    elif classifier == "call":
        if fn == "nodeManagementSubscriptionRequestCall":
            record_subscription(cmd_items, state, "subscription")
            send_result0(sock, our_addr, dg, "SubscriptionAck")
        elif fn == "nodeManagementBindingRequestCall":
            record_subscription(cmd_items, state, "binding")
            send_result0(sock, our_addr, dg, "BindingAck")
        else:
            print(f"  (unhandled CALL: {fn})")
            if _hdr_get(header, "ackRequest"):
                send_result0(sock, our_addr, dg, "GenericAck")

    elif classifier == "write":
        if "loadControlLimitListData" in fns:
            handle_limit_write(sock, our_addr, dg, cmd_items, state)
        elif "deviceConfigurationKeyValueListData" in fns:
            handle_config_write(sock, our_addr, dg, cmd_items, state)
        else:
            print(f"  (unhandled WRITE: {fns})")
            send_result0(sock, our_addr, dg, "GenericWriteAck")

    elif classifier == "result":
        ref = None
        for item in header:
            if "msgCounterReference" in item:
                ref = item["msgCounterReference"]
        print(f"  <- RESULT (msgCounterRef={ref})")

    elif classifier in ("reply", "notify"):
        print(f"  <- {classifier.upper()} {fn} (informational)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="EEBus fake wallbox (CS/LPC) for EG2 testing")
    ap.add_argument("host", help="HEMS IP address")
    ap.add_argument("--port", type=int, default=4714, help="eebus_eg2 SHIP port (default 4714)")
    ap.add_argument("--duration", type=int, default=300,
                    help="Seconds to keep serving after connect (default 300)")
    ap.add_argument("--no-pairing-trigger", action="store_true",
                    help="Skip the HTTP pairing-mode button press")
    args = ap.parse_args()

    print("=" * 60)
    print("EEBus Fake Wallbox (CS/LPC ControllableSystem, EVSE)")
    print("=" * 60)

    cert_pem, key_pem, our_ski = fs.load_or_generate_cert()
    print(f"Fake Wallbox SKI: {our_ski}")

    if not args.no_pairing_trigger:
        try:
            import urllib.request
            url = f"http://{args.host}/button/esphome_hems_eg2_pairing-modus_starten/press"
            req = urllib.request.Request(url, data=b"", method="POST")
            req.add_header("Content-Length", "0")
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
            print(f"[Pairing] Triggered EG2 pairing mode ({url})")
        except Exception as e:
            print(f"[Pairing] Warning: could not trigger EG2 pairing mode: {e}")

    cert_file = key_file = None
    sock = None
    state = WallboxState()
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

        eg_ski = fs.get_peer_ski(sock)
        print(f"EG2 SKI (from TLS cert): {eg_ski}")

        our_addr = f"d:_n:FakeWallbox_{our_ski[:8]}"
        print(f"SPINE our addr : {our_addr}")

        fs.ws_upgrade(sock, args.host, args.port)
        print("WebSocket upgraded")

        local_id = f"FakeWallbox-{our_ski[:8]}"
        server_id = fs.ship_handshake(sock, local_id)
        print(f"\nSHIP DataExchange reached — EG2 SHIP ID: '{server_id}'")

        print(f"\n[Serve] Responding to EG2 SPINE requests for {args.duration} s...")
        print("[Serve] Run fake_steuerbox.py --scenario in parallel to test the full chain.\n")

        deadline = time.monotonic() + args.duration
        next_heartbeat = time.monotonic() + 30.0
        sock.settimeout(1.0)
        while time.monotonic() < deadline:
            if time.monotonic() >= next_heartbeat:
                send_heartbeat_notify(sock, our_addr, state)
                next_heartbeat = time.monotonic() + 30.0
            try:
                frame = fs.ws_recv(sock)
            except (TimeoutError, ssl.SSLWantReadError):
                continue
            except OSError as e:
                if isinstance(e, ConnectionError):
                    raise
                continue
            msg_type, body = frame[0], frame[1:]
            if msg_type != fs.MSG_DATA:
                print(f"  <- [CTRL] {body[:120]!r}")
                continue
            try:
                parsed = json.loads(body)
            except Exception:
                print(f"  <- [DATA] (unparseable) {body[:80]!r}")
                continue
            print(f"  <- [DATA] {json.dumps(parsed)[:240]}")
            handle_datagram(sock, our_addr, parsed, state)

        print("\n[Serve] Duration elapsed — summary of received LPC limit writes:")
        if not state.received_writes:
            print("  (none received)")
        for ts, limit_id, watts, active in state.received_writes:
            print(f"  {ts}  limitId={limit_id}  {watts} W  active={active}")

    except ConnectionError as e:
        print(f"\nERROR: {e}")
        if state.received_writes:
            print("Received LPC limit writes before disconnect:")
            for ts, limit_id, watts, active in state.received_writes:
                print(f"  {ts}  limitId={limit_id}  {watts} W  active={active}")
        sys.exit(1)
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        for f in (cert_file, key_file):
            if f and os.path.exists(f):
                os.unlink(f)


if __name__ == "__main__":
    main()
