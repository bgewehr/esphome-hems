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
    data = val.encode()
    return encode_varint((field_num << 3) | 2) + encode_varint(len(data)) + data

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
    st = -1
    msg = ''
    while pos + 9 <= len(resp):
        flen = int.from_bytes(resp[pos:pos+3], 'big')
        ftype = resp[pos+3]
        payload = resp[pos+9:pos+9+flen]
        if ftype == 0 and len(payload) >= 5:
            result_data += payload[5:]
        elif ftype == 1:
            try:
                for n,v in dec.decode(payload):
                    if n=='grpc-status': st=int(v)
                    if n=='grpc-message': msg=v
            except: pass
        pos += 9 + flen
    s.close()
    return result_data, st, msg

def read_varint(data, i):
    val=0; shift=0
    while i<len(data):
        b=data[i]; i+=1; val|=(b&0x7f)<<shift; shift+=7
        if not(b&0x80): return val,i
    return val,i

req = encode_string_field(1, SKI)

print("=== GetConsumptionNominalMax (max. Leistungsaufnahme) ===")
d,s,m = grpc_call('/eebus.v1.LPCService/GetConsumptionNominalMax', req)
print(f"  status={s} msg={m}")
if d:
    i=0
    while i<len(d):
        tag=d[i]; i+=1; fn=tag>>3; wt=tag&7
        if wt==1:
            v=struct.unpack_from('<d',d,i)[0]; i+=8
            print(f"  watts: {v}")

print()
print("=== GetFailsafeLimit ===")
d,s,m = grpc_call('/eebus.v1.LPCService/GetFailsafeLimit', req)
print(f"  status={s} msg={m}")
if d:
    i=0
    while i<len(d):
        tag=d[i]; i+=1; fn=tag>>3; wt=tag&7
        if wt==1:
            v=struct.unpack_from('<d',d,i)[0]; i+=8
            names={1:'value_watts',2:'duration_min_seconds'}
            print(f"  {names.get(fn,'field_'+str(fn))}: {v}")
        elif wt==0:
            v,i=read_varint(d,i)
            names={1:'value_watts',2:'duration_min_seconds'}
            print(f"  {names.get(fn,'field_'+str(fn))}: {v}")

print()
print("=== GetEnergyConsumed ===")
d,s,m = grpc_call('/eebus.v1.MonitoringService/GetEnergyConsumed', req)
print(f"  status={s} msg={m}")
if d:
    i=0
    while i<len(d):
        tag=d[i]; i+=1; fn=tag>>3; wt=tag&7
        if wt==1:
            v=struct.unpack_from('<d',d,i)[0]; i+=8
            print(f"  kilowatt_hours: {v} kWh")
        elif wt==2:
            slen,i=read_varint(d,i)
            print(f"  field {fn} (timestamp, {slen} bytes)"); i+=slen

print()
print("=== GetMeasurements (alle Messwerte) ===")
d,s,m = grpc_call('/eebus.v1.MonitoringService/GetMeasurements', req)
print(f"  status={s} msg={m}")
if d:
    i=0
    while i<len(d):
        tag=d[i]; i+=1; fn=tag>>3; wt=tag&7
        if wt==2:
            slen,i=read_varint(d,i)
            entry=d[i:i+slen]; i+=slen
            j=0; typ=''; val=0.0; unit=''
            while j<len(entry):
                t=entry[j]; j+=1; efn=t>>3; ewt=t&7
                if ewt==2:
                    sl,j=read_varint(entry,j)
                    sv=entry[j:j+sl]; j+=sl
                    try:
                        decoded=sv.decode()
                        if efn==1: typ=decoded
                        elif efn==3: unit=decoded
                    except: pass  # timestamp sub-message
                elif ewt==1:
                    val=struct.unpack_from('<d',entry,j)[0]; j+=8
                elif ewt==0:
                    v,j=read_varint(entry,j)
            print(f"  {typ}: {val} {unit}")

print()
print("=== GetConsumptionLimit (aktuell) ===")
d,s,m = grpc_call('/eebus.v1.LPCService/GetConsumptionLimit', req)
print(f"  status={s} msg={m}")
if d:
    i=0
    while i<len(d):
        tag=d[i]; i+=1; fn=tag>>3; wt=tag&7
        if wt==1:
            v=struct.unpack_from('<d',d,i)[0]; i+=8
            print(f"  value_watts: {v}")
        elif wt==0:
            v,i=read_varint(d,i)
            names={2:'duration_seconds',3:'is_active',4:'is_changeable'}
            print(f"  {names.get(fn,'field_'+str(fn))}: {v}")

print()
print("=== ListPairedDevices (mit Klassifikation) ===")
d,s,m = grpc_call('/eebus.v1.DeviceService/ListPairedDevices', b'')
print(f"  status={s} msg={m}")
if d:
    i=0
    while i<len(d):
        tag=d[i]; i+=1; fn=tag>>3; wt=tag&7
        if wt==2:
            slen,i=read_varint(d,i)
            entry=d[i:i+slen]; i+=slen
            j=0; fields={}
            while j<len(entry):
                t=entry[j]; j+=1; efn=t>>3; ewt=t&7
                if ewt==2:
                    sl,j=read_varint(entry,j)
                    sv=entry[j:j+sl]; j+=sl
                    fields.setdefault(efn,[]).append(sv.decode())
                elif ewt==0:
                    v,j=read_varint(entry,j)
                    fields[efn]=v
            names={1:'SKI',2:'Brand',3:'Model',4:'Serial',5:'DeviceType',6:'UseCases'}
            for k,v in sorted(fields.items()):
                print(f"  {names.get(k,'field_'+str(k))}: {v}")
