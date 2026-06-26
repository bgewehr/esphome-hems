import socket, struct, time, hpack

from env_config import get_eebus_endpoint, get_eebus_ski

host, port = get_eebus_endpoint()
SKI = get_eebus_ski()

def encode_varint(val):
    out = bytearray()
    while val > 0x7f:
        out.append((val & 0x7f) | 0x80)
        val >>= 7
    out.append(val)
    return bytes(out)

def encode_string_field(field_num, val):
    data = val.encode() if isinstance(val, str) else val
    return encode_varint((field_num << 3) | 2) + encode_varint(len(data)) + data

def grpc_call(path, proto_payload):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((host, port))
    preface = b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'
    settings = b'\x00\x00\x00\x04\x00\x00\x00\x00\x00'
    s.sendall(preface + settings)
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
    grpc_st = -1; grpc_msg_str = ''
    while pos + 9 <= len(resp):
        flen = int.from_bytes(resp[pos:pos+3], 'big')
        ftype = resp[pos+3]
        payload = resp[pos+9:pos+9+flen]
        if ftype == 0 and len(payload) > 5:
            result_data += payload[5:]
        elif ftype == 1:
            try:
                hdrs = dec.decode(payload)
                for n,v in hdrs:
                    if n == 'grpc-status': grpc_st = int(v)
                    if n == 'grpc-message': grpc_msg_str = v
            except: pass
        pos += 9 + flen
    s.close()
    return result_data, grpc_st, grpc_msg_str

device_req = encode_string_field(1, SKI)

print('=== GetHeartbeatStatus ===')
data, st, msg = grpc_call('/eebus.v1.LPCService/GetHeartbeatStatus', device_req)
print(f'  grpc_status={st} msg={msg}')
if data:
    i = 0
    while i < len(data):
        tag = data[i]; i += 1
        fn = tag >> 3; wt = tag & 7
        if wt == 0:
            val = 0; shift = 0
            while i < len(data):
                b = data[i]; i += 1
                val |= (b & 0x7f) << shift; shift += 7
                if not (b & 0x80): break
            names = {1: 'running', 2: 'within_duration'}
            print(f'  {names.get(fn, f"field_{fn}")}: {bool(val)}')
        else:
            break
else:
    print('  (empty response)')

print()
print('=== StartHeartbeat ===')
data, st, msg = grpc_call('/eebus.v1.LPCService/StartHeartbeat', device_req)
print(f'  grpc_status={st} msg={msg}')

print()
print('=== GetHeartbeatStatus (after start) ===')
data, st, msg = grpc_call('/eebus.v1.LPCService/GetHeartbeatStatus', device_req)
print(f'  grpc_status={st} msg={msg}')
if data:
    i = 0
    while i < len(data):
        tag = data[i]; i += 1
        fn = tag >> 3; wt = tag & 7
        if wt == 0:
            val = 0; shift = 0
            while i < len(data):
                b = data[i]; i += 1
                val |= (b & 0x7f) << shift; shift += 7
                if not (b & 0x80): break
            names = {1: 'running', 2: 'within_duration'}
            print(f'  {names.get(fn, f"field_{fn}")}: {bool(val)}')
        else:
            break
else:
    print('  (empty response)')
