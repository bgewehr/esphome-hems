import socket, struct, time, hpack

from env_config import get_eebus_endpoint

host, port = get_eebus_endpoint()

def grpc_call(path, proto_payload):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((host, port))
    s.sendall(b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n\x00\x00\x00\x04\x00\x00\x00\x00\x00')
    s.recv(4096)
    s.sendall(b'\x00\x00\x00\x04\x01\x00\x00\x00\x00\x00\x00\x04\x08\x00\x00\x00\x00\x00\x00\x0f\xff\xff')
    hp = bytearray()
    hp.append(0x83); hp.append(0x86)
    hp.append(0x04); hp.append(len(path)); hp.extend(path.encode())
    hp.append(0x00); hp.append(10); hp.extend(b':authority'); hp.append(len(host)); hp.extend(host.encode())
    hp.append(0x00); hp.append(12); hp.extend(b'content-type'); hp.append(16); hp.extend(b'application/grpc')
    hp.append(0x00); hp.append(2); hp.extend(b'te'); hp.append(8); hp.extend(b'trailers')
    hdr = struct.pack('>I', len(hp))[1:] + b'\x01\x04\x00\x00\x00\x01' + bytes(hp)
    grpc_msg = b'\x00' + struct.pack('>I', len(proto_payload)) + proto_payload
    df = struct.pack('>I', len(grpc_msg))[1:] + b'\x00\x01\x00\x00\x00\x01' + grpc_msg
    s.sendall(hdr + df)
    time.sleep(0.5)
    resp = s.recv(4096)
    pos = 0
    result_data = b''
    dec = hpack.Decoder()
    while pos + 9 <= len(resp):
        flen = int.from_bytes(resp[pos:pos+3], 'big')
        ftype = resp[pos+3]
        flags = resp[pos+4]
        payload = resp[pos+9:pos+9+flen]
        ftypes = {0:'DATA', 1:'HEADERS', 4:'SETTINGS', 6:'PING', 8:'WINDOW_UPDATE'}
        print(f"  frame: {ftypes.get(ftype, ftype)} len={flen} flags=0x{flags:02x}")
        if ftype == 0 and len(payload) > 5:
            result_data += payload[5:]
        elif ftype == 1:
            try:
                hdrs = dec.decode(payload)
                for n,v in hdrs:
                    print(f"    {n}: {v}")
            except Exception as e:
                print(f"    hpack error: {e}")
        pos += 9 + flen
    s.close()
    return result_data

def read_varint(data, i):
    val = 0; shift = 0
    while i < len(data):
        b = data[i]; i += 1
        val |= (b & 0x7f) << shift; shift += 7
        if not (b & 0x80): return val, i
    return val, i

def decode_fields(data):
    fields = {}
    i = 0
    while i < len(data):
        tag = data[i]; i += 1
        fn = tag >> 3; wt = tag & 7
        if wt == 0:
            val, i = read_varint(data, i)
            fields.setdefault(fn, []).append(val)
        elif wt == 2:
            slen, i = read_varint(data, i)
            fields.setdefault(fn, []).append(data[i:i+slen])
            i += slen
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        else:
            break
    return fields

# Check bridge status first
print("=== DeviceService/GetStatus ===")
data = grpc_call('/eebus.v1.DeviceService/GetStatus', b'')
if data:
    fields = decode_fields(data)
    for f, vals in fields.items():
        for v in vals:
            print(f"  field {f}: {v.decode() if isinstance(v, bytes) else v}")
else:
    print("  (empty)")

print()
print("=== DeviceService/ListDiscoveredDevices ===")
data = grpc_call('/eebus.v1.DeviceService/ListDiscoveredDevices', b'')
if data:
    outer = decode_fields(data)
    for dev_bytes in outer.get(1, []):
        dev = decode_fields(dev_bytes)
        print(f"  discovered: {dev.get(1, [b''])[0].decode() if dev.get(1) else '?'}")
else:
    print("  (no devices discovered)")

print()
# PairedDevice: ski(1), brand(2), model(3), serial(4), device_type(5), supported_use_cases(6)
print("=== DeviceService/ListPairedDevices ===")
data = grpc_call('/eebus.v1.DeviceService/ListPairedDevices', b'')
if data:
    outer = decode_fields(data)
    for dev_bytes in outer.get(1, []):
        dev = decode_fields(dev_bytes)
        print("Paired Device:")
        print(f"  SKI:        {dev.get(1, [b''])[0].decode()}")
        print(f"  Brand:      {dev.get(2, [b''])[0].decode()}")
        print(f"  Model:      {dev.get(3, [b''])[0].decode()}")
        print(f"  Serial:     {dev.get(4, [b''])[0].decode()}")
        print(f"  DeviceType: {dev.get(5, [b''])[0].decode()}")
        use_cases = [uc.decode() for uc in dev.get(6, [])]
        print(f"  UseCases:   {use_cases}")
else:
    print("No data received")
