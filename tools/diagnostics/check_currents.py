from pymodbus.client import ModbusTcpClient
import struct

from env_config import get_fronius_endpoint

host, port = get_fronius_endpoint()
c = ModbusTcpClient(host, port=port)
c.connect()

# Read current registers 40072-40075 (AphA, AphB, AphC, A_SF) from Grid Meter (device 240)
r = c.read_holding_registers(40072, count=4, device_id=240)
print(f"Raw regs 40072-40075: {r.registers}")
for i, name in enumerate(["AphA", "AphB", "AphC", "A_SF"]):
    raw = r.registers[i]
    signed = struct.unpack(">h", struct.pack(">H", raw))[0]
    print(f"  {name} (40{72+i}): unsigned={raw}, signed={signed}")

# Read power register 40087 (W) from Grid Meter
r2 = c.read_holding_registers(40087, count=1, device_id=240)
signed_power = struct.unpack(">h", struct.pack(">H", r2.registers[0]))[0]
print(f"\nGrid Power (40087): unsigned={r2.registers[0]}, signed={signed_power}")

# Read per-phase power registers if available (Model 203: WphA=40088, WphB=40089, WphC=40090)
r3 = c.read_holding_registers(40088, count=4, device_id=240)
print(f"\nPer-phase power regs 40088-40091:")
for i, name in enumerate(["WphA", "WphB", "WphC", "W_SF"]):
    raw = r3.registers[i]
    signed = struct.unpack(">h", struct.pack(">H", raw))[0]
    print(f"  {name} (40{88+i}): unsigned={raw}, signed={signed}")

# Also check total current register 40071
r4 = c.read_holding_registers(40071, count=1, device_id=240)
print(f"\nTotal Current A (40071): unsigned={r4.registers[0]}, signed={struct.unpack('>h', struct.pack('>H', r4.registers[0]))[0]}")

c.close()
