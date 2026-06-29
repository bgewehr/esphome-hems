from pymodbus.client import ModbusTcpClient
import struct

from env_config import get_fronius_endpoint

host, port = get_fronius_endpoint()
c = ModbusTcpClient(host, port=port)
c.connect()

def read_meter(unit_id, label):
    print(f"\n=== {label} (unit 0x{unit_id:02X}={unit_id}) ===")
    r = c.read_holding_registers(40072, count=4, device_id=unit_id)
    print(f"Raw regs 40072-40075: {r.registers}")
    for i, name in enumerate(["AphA", "AphB", "AphC", "A_SF"]):
        raw = r.registers[i]
        signed = struct.unpack(">h", struct.pack(">H", raw))[0]
        print(f"  {name} (40{72+i}): unsigned={raw}, signed={signed}")
    r2 = c.read_holding_registers(40087, count=1, device_id=unit_id)
    signed_power = struct.unpack(">h", struct.pack(">H", r2.registers[0]))[0]
    print(f"W (40087): unsigned={r2.registers[0]}, signed={signed_power}")
    r3 = c.read_holding_registers(40091, count=1, device_id=unit_id)
    signed_wsf = struct.unpack(">h", struct.pack(">H", r3.registers[0]))[0]
    print(f"W_SF (40091): unsigned={r3.registers[0]}, signed={signed_wsf}")

read_meter(0xF0, "Grid Meter")
read_meter(0xF1, "HP Meter")
read_meter(0xF2, "EV Meter")

c.close()
