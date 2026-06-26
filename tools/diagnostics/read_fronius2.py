"""
Fronius Gen24 - Saubere Analyse (ioBroker gestoppt, kein Konflikt)
"""
from pymodbus.client import ModbusTcpClient
import struct

from env_config import get_fronius_endpoint

host, port = get_fronius_endpoint()
c = ModbusTcpClient(host, port=port)
c.connect()

def read_reg(addr, device_id=1):
    r = c.read_holding_registers(addr, device_id=device_id)
    if r.isError():
        return None, None
    raw = r.registers[0]
    signed = struct.unpack('>h', struct.pack('>H', raw))[0]
    return raw, signed

print("=== FRONIUS GEN24 REGISTER (ioBroker gestoppt) ===\n")

# AC Power
print("--- AC POWER (Inverter, device_id=1) ---")
raw, signed = read_reg(40083)
_, sf = read_reg(40084)
print(f"  addr 40083 (W):    u16={raw}, i16={signed}")
print(f"  addr 40084 (W_SF): i16={sf}")
if sf is not None and -4 <= sf <= 4:
    print(f"  → AC Power = {signed} * 10^{sf} = {signed * (10**sf):.1f} W")

# Solar DC Total
print("\n--- SOLAR DC TOTAL ---")
raw, signed = read_reg(40100)
_, sf = read_reg(40101)
print(f"  addr 40100 (DCW):    u16={raw}, i16={signed}")
print(f"  addr 40101 (DCW_SF): i16={sf}")
if sf is not None and -4 <= sf <= 4:
    print(f"  → DC Total = {raw} * 10^{sf} = {raw * (10**sf):.1f} W")

# MPPT
print("\n--- MPPT1 + MPPT2 ---")
mppt1, _ = read_reg(40274)
mppt2, _ = read_reg(40294)
_, sf_mppt = read_reg(40257)
print(f"  addr 40274 (MPPT1): u16={mppt1}")
print(f"  addr 40294 (MPPT2): u16={mppt2}")
print(f"  addr 40257 (SF):    i16={sf_mppt}")
if sf_mppt is not None and -4 <= sf_mppt <= 4:
    print(f"  → MPPT1 = {mppt1} * 10^{sf_mppt} = {mppt1 * (10**sf_mppt):.1f} W")
    print(f"  → MPPT2 = {mppt2} * 10^{sf_mppt} = {mppt2 * (10**sf_mppt):.1f} W")
    print(f"  → Summe = {(mppt1+mppt2) * (10**sf_mppt):.1f} W")

# Battery
print("\n--- BATTERY ---")
charge, _ = read_reg(40314)
discharge, _ = read_reg(40334)
print(f"  addr 40314 (Charge):    u16={charge}")
print(f"  addr 40334 (Discharge): u16={discharge}")
print(f"  SF = {sf_mppt} (addr 40257)")
if sf_mppt is not None and -4 <= sf_mppt <= 4:
    print(f"  → Charge = {charge * (10**sf_mppt):.1f} W")
    print(f"  → Discharge = {discharge * (10**sf_mppt):.1f} W")
    print(f"  → Netto (discharge - charge) = {(discharge - charge) * (10**sf_mppt):.1f} W")

# Grid (Smart Meter, device_id=240)
print("\n--- GRID (Smart Meter, device_id=240) ---")
raw, signed = read_reg(40087, device_id=240)
_, sf_grid = read_reg(40091, device_id=240)
print(f"  addr 40087 (W, dev=240):    i16={signed}")
print(f"  addr 40091 (W_SF, dev=240): i16={sf_grid}")
if sf_grid is not None and -4 <= sf_grid <= 4:
    print(f"  → Grid Power = {signed} * 10^{sf_grid} = {signed * (10**sf_grid):.1f} W")

c.close()
