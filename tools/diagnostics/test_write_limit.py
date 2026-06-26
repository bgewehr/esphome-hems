import socket, struct, time, hpack

from env_config import get_eebus_endpoint, get_eebus_ski

host, port = get_eebus_endpoint()
SKI = get_eebus_ski()

def ev(val):
    out = bytearray()
    while val > 0x7f:
        out.append((val & 0x7f) | 0x80)
        val >>= 7
    out.append(val)
    return bytes(out)

def gc(path, pp):
    s = socket.socket()
    s.settimeout(5)
    s.connect((host, port))
    s.sendall(b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n\x00\x00\x00\x04\x00\x00\x00\x00\x00')
    s.recv(4096)
    s.sendall(b'\x00\x00\x00\x04\x01\x00\x00\x00\x00\x00\x00\x04\x08\x00\x00\x00\x00\x00\x00\x0f\xff\xff')
    hp = bytearray()
    hp.append(0x83); hp.append(0x86)
    hp.append(0x04); hp.append(len(path)); hp.extend(path.encode())
    hp.append(0); hp.append(10); hp.extend(b':authority'); hp.append(len(host)); hp.extend(host.encode())
    hp.append(0); hp.append(12); hp.extend(b'content-type'); hp.append(16); hp.extend(b'application/grpc')
    hp.append(0); hp.append(2); hp.extend(b'te'); hp.append(8); hp.extend(b'trailers')
    hdr = struct.pack('>I', len(hp))[1:] + b'\x01\x04\x00\x00\x00\x01' + bytes(hp)
    gm = b'\x00' + struct.pack('>I', len(pp)) + pp
    df = struct.pack('>I', len(gm))[1:] + b'\x00\x01\x00\x00\x00\x01' + gm
    s.sendall(hdr + df)
    time.sleep(0.5)
    resp = s.recv(4096)
    s.close()
    dec = hpack.Decoder()
    pos = 0; st = -1; data = b''
    while pos + 9 <= len(resp):
        flen = int.from_bytes(resp[pos:pos+3], 'big')
        ft = resp[pos+3]
        pl = resp[pos+9:pos+9+flen]
        if ft == 0 and len(pl) >= 5:
            data += pl[5:]
        elif ft == 1:
            try:
                for n, v in dec.decode(pl):
                    if n == 'grpc-status': st = int(v)
            except:
                pass
        pos += 9 + flen
    return data, st

def write_limit(watts, active=True):
    req = bytearray()
    req += ev((1 << 3) | 2) + ev(len(SKI)) + SKI.encode()
    req += ev((2 << 3) | 1) + struct.pack('<d', watts)
    req += ev((4 << 3) | 0) + ev(1 if active else 0)
    return bytes(req)

def read_limit():
    req = ev((1 << 3) | 2) + ev(len(SKI)) + SKI.encode()
    data, st = gc('/eebus.v1.LPCService/GetConsumptionLimit', req)
    if data:
        i = 0
        while i < len(data):
            tag = data[i]; i += 1
            fn = tag >> 3; wt = tag & 7
            if wt == 1:
                v = struct.unpack_from('<d', data, i)[0]; i += 8
                print(f'  field {fn} (double): {v}')
            elif wt == 0:
                v = 0; sh = 0
                while i < len(data):
                    b = data[i]; i += 1
                    v |= (b & 0x7f) << sh; sh += 7
                    if not (b & 0x80):
                        break
                print(f'  field {fn} (varint): {v}')
    return st

print('1. Read current limit:')
read_limit()

print()
print('2. Write 3200W is_active=true:')
_, st = gc('/eebus.v1.LPCService/WriteConsumptionLimit', write_limit(3200.0, True))
print(f'  grpc_status={st}')

time.sleep(2)
print()
print('3. Read limit after write:')
read_limit()

print()
print('4. Write 4200W is_active=true (restore):')
_, st = gc('/eebus.v1.LPCService/WriteConsumptionLimit', write_limit(4200.0, True))
print(f'  grpc_status={st}')

time.sleep(2)
print()
print('5. Read limit after restore:')
read_limit()
