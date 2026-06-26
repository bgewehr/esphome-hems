"""
Fronius Gen24 - Empirische Analyse
Fronius dashboard: Solar ~3.4kW, Battery charging ~3kW, AC ~420W
Hypothese: ESPHome sendet die Adresse 1:1 auf den Wire (0-basiert)
ioBroker Template: Register = Adresse (ioBroker NICHT -1)
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

# Fronius aktuell: Solar ~3.4kW, AC ~420W, Battery Charge ~3kW
# Suche nach einem Wert ~34000 (mit SF=-1) oder ~3400 (SF=0)
# oder ~340000 (SF=-2) irgendwo im Solar-Bereich

print("=== SUCHE NACH SOLAR ~3400W ===")
print("Erwartete Rohwerte: 3400 (SF=0), 34000 (SF=-1), 340000 (SF=-2 → 2 Register)")
print()

# Vollständiger Scan des MPPT-Modells (um 40270-40300)
print("--- MPPT Bereich addr 40270-40300 ---")
for addr in range(40270, 40300):
    raw, signed = read_reg(addr)
    if raw is not None and raw != 0 and raw != 65535 and raw != 32768:
        print(f"  addr {addr}: u16={raw:6d}, i16={signed:6d}  → *0.1={raw*0.1:.0f} *0.01={raw*0.01:.0f}")

# Der ESP hat bei bat_dbg raw=30167 sf=-1 für addr 40314 gezeigt.
# 30167 * 0.1 = 3016.7W ← das passt zu Battery Charge!
# ESP liest address:40314 → addr 40314 → raw=30674 (oder ähnlich schwankend)
# 30674 * 0.1 = 3067.4W ← PASST!

# Also nutzt ESPHome addr 40314 direkt und bekommt den Charge-Wert!
# Dann ist SF=-1 der richtige SF.
# Aber ESP zeigte sf=-1 aus dem Global, gelesen aus addr 40257.
# Unsere pymodbus-Lesung: addr 40257 = -1, addr 40256 = -2
# ESP liest address:40257 → bekommt -1 ← KORREKT!

print("\n--- BESTÄTIGUNG: ESPHome-Adressen DIREKT (kein Offset) ---")
print(f"  addr 40314 (Battery Charge): {read_reg(40314)[0]} → *0.1 = {read_reg(40314)[0]*0.1:.0f}W")
print(f"  addr 40334 (Battery Discharge): {read_reg(40334)[0]}")
print(f"  addr 40257 (Battery SF): {read_reg(40257)[1]}")
print()

# AC Power: ESP zeigt 304W mit sf_ac_power
# addr 40083 = 4144, mit SF aus addr 40084 = -1 → 414W ← PASST NICHT zu ESP 304W
# ABER: ESP liest SF aus address:40084 → addr 40084 = -1
# ESP liest AC Power aus address:40083 → addr 40083 = 4144
# ESP berechnet: 4144 * 10^(-1) = 414W (nicht 304W!)
# → ESP sf_ac_power ist NICHT -1, sondern 0 (initial!)
# ESP zeigt: x * pow(10, sf_ac_power) mit sf_ac_power=0 → 304 * 1 = 304W
# Also: das Raw ist 304, nicht 4144. Der ESP liest eine andere Adresse!

# Warte - ESP nutzt S_WORD. Wenn addr 40083 = 4144 als S_WORD gelesen wird, ist es 4144.
# Aber vorher war AC Power korrekt "bis grade war alles gut". Das ESP hat immer addr 40083 genutzt.
# Der Fronius-Wert SCHWANKT! Lass uns mehrfach lesen:
print("--- AC POWER: Mehrfach-Lesung addr 40083 ---")
for i in range(5):
    raw, signed = read_reg(40083)
    print(f"  Lesung {i+1}: u16={raw}, i16={signed}")

print("\n--- SOLAR: Mehrfach-Lesung addr 40100 ---")
for i in range(5):
    raw, signed = read_reg(40100)
    print(f"  Lesung {i+1}: u16={raw}, i16={signed}")

# ioBroker nutzt AUCH address 40314 (ohne offset) laut Screenshot!
# Also stimmt die Adressierung: address = wire-address
# Problem: Solar zeigt ~4285 bei addr 40100 → 4285*0.1 = 428.5W ← ZU WENIG
# Aber ioBroker zeigt korrekt... MOMENT: ioBroker hat addr 40314 = "DC Power charge"
# und addr 40334 = "DC Power discharge". Die sind unsigned 16 bit.
# 
# VIELLEICHT: Solar Power ist NICHT bei Register 40100 für die Gesamtleistung!
# Register 40100 ist DCW aber das ist evtl. nur der WR-Output (ohne Battery)
# Die echte "Solar" im Fronius Dashboard = MPPT1 + MPPT2
# MPPT1 bei 40274, MPPT2 bei 40294

print("\n--- MPPT1 (addr 40274) + MPPT2 (addr 40294) ---")
mppt1, _ = read_reg(40274)
mppt2, _ = read_reg(40294)
sf_mppt = read_reg(40257)[1]
print(f"  addr 40274 (MPPT1): u16={mppt1}, *10^{sf_mppt} = {mppt1 * (10**sf_mppt):.1f}W")
print(f"  addr 40294 (MPPT2): u16={mppt2}, *10^{sf_mppt} = {mppt2 * (10**sf_mppt):.1f}W")
print(f"  Summe: {(mppt1+mppt2) * (10**sf_mppt):.1f}W")

c.close()
