"""
Probe Fronius Gen24 Modbus TCP for Ohmpilot data.

The ESPHome modbustcp component sends `address: 40087` directly as the
FC03 PDU start address — so we do the same here (no subtraction of 40001).

Usage:
    pip install pymodbus
    python tools/probe_fronius_modbus.py
"""

import sys
from pymodbus.client import ModbusTcpClient

HOST = "192.168.178.163"
PORT = 502

KNOWN_UNITS = {
    0xF0:  "Grid Meter (240)",
    0xF1:  "HP Meter (241)",
    0xF2:  "EV Meter (242)",
}
PROBE_UNITS = [0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8]

# FC03 addresses matching ESPHome YAML (address as-is, no subtraction)
REG_W_TOTAL = 40087  # S_WORD, Total Real Power W
REG_W_SF    = 40091  # S_WORD, Scale Factor


def read_regs(client, unit, address, count=1):
    r = client.read_holding_registers(address, count=count, device_id=unit)
    if r.isError():
        return None
    return r.registers


def s16(v):
    return v if v < 0x8000 else v - 0x10000


def probe_unit(client, unit, label=""):
    tag = f"  Unit 0x{unit:02X}/{unit}{' (' + label + ')' if label else ''}"

    # Alive check: read one register at W_Total
    regs = read_regs(client, unit, REG_W_TOTAL, count=5)
    if regs is None:
        print(f"{tag}: no response at reg {REG_W_TOTAL}")
        return

    print(f"{tag}: responded!")

    w_raw  = s16(regs[0])                    # reg 40087
    sf_raw = s16(regs[REG_W_SF - REG_W_TOTAL])  # reg 40091 = offset 4

    if -4 <= sf_raw <= 4:
        power = w_raw * (10 ** sf_raw)
        print(f"    W_Total  (40087) = {w_raw:6d}  SF (40091) = {sf_raw:+d}  -> {power:.1f} W")
    else:
        print(f"    W_Total  (40087) = {w_raw:6d}  SF (40091) = {sf_raw} (SF out of range)")

    # Dump registers 40083–40102 for inspection
    dump = read_regs(client, unit, 40083, count=20)
    if dump:
        print(f"    Dump 40083–40102:")
        for i, v in enumerate(dump):
            print(f"      {40083+i:5d}: 0x{v:04X}  raw={v:5d}  s16={s16(v):+6d}")


def main():
    print(f"Connecting to {HOST}:{PORT} ...")
    client = ModbusTcpClient(HOST, port=PORT, timeout=3)
    if not client.connect():
        print("ERROR: connection failed")
        sys.exit(1)
    print("Connected.\n")

    print("=== Known units (sanity check) ===")
    for unit, label in KNOWN_UNITS.items():
        probe_unit(client, unit, label)

    print("\n=== Probing Ohmpilot candidate unit IDs ===")
    for unit in PROBE_UNITS:
        probe_unit(client, unit)

    client.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
