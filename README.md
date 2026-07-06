# esphome-hems

ESPHome configuration for a HEMS controller based on the **Waveshare ESP32-S3-POE-ETH-8DI-8RO**.

The controller receives §14a EnWG power limits from the grid operator (VNB) via EEBus SHIP/SPINE and distributes them internally to controllable consumption units (StVE) such as heat pumps and wallboxes — also via EEBus.

---

## Overview

```
VNB gateway (CLS Steuerbox)
        │ EEBus LPC  (CS role, port 4712)
        ▼
  ESP32 HEMS  ──── §14a limit ────►  Heat pump  (EG role, port 4713)
  (this controller)                 (EEBus LPC)
        │
        └────────────────────────►  Wallbox, further StVE ...
```

The controller combines:

- **EEBus CS receiver** (`eebus_cs`): receives §14a limits from the VNB's CLS Steuerbox
- **EEBus EG sender** (`eebus_eg`): sends limits to EEBus-capable devices (heat pump, wallbox)
- **OSSHPCF/SEMP**: passive monitoring of compressor flexibility (PV self-consumption)
- **MPC**: reads current power consumption of devices via EEBus
- **Xemex CSMB Modbus RTU** server emulation
- **Fronius Gen24** Modbus TCP monitoring and battery discharge limiting
- **S0 pulse counting** (water meter)
- **8 relay outputs / 8 digital inputs**

---

## Repository structure

```
.
├── esphome-hems.yaml          # Core config: hw, api/ota/logger, time, eebus_cs glue
├── dido.yaml                  # DI/DO hardware: i2c, pca9554, relay_2-8, DI3-8
├── eebus-cs.yaml              # EEBus CS + §14a hardware (relay_1, DI1, LED, switches)
├── eebus-eg-1.yaml            # EEBus EG1 instance (e.g. heat pump, port 4713)
├── eebus-eg-2.yaml            # EEBus EG2 instance (e.g. wallbox, port 4714)
├── fronius-gen24.yaml         # Fronius Gen24 Modbus TCP + battery control
├── xemex-csmb.yaml            # Xemex CSMB Modbus RTU server emulation
├── s0-meter-1.yaml            # S0 pulse counter (water meter, DI2)
├── compile.ps1                # Compile/flash script (Windows PowerShell)
├── requirements-dev.txt       # Python dependencies for diagnostic scripts
├── secrets.example.yaml       # Template for local credentials
├── components/
│   ├── eebus_cs/              # EEBus CS component (LPC receiver from VNB)
│   └── eebus_eg/              # EEBus EG component (LPC sender to StVE)
├── openeebus/                 # openeebus SHIP/SPINE C library (submodule)
├── port/                      # ESP32 port adaptations for openeebus
└── tools/
    └── diagnostics/           # EEBus and Fronius diagnostic scripts
```

The package files are self-contained: each owns its entities, sorting groups, and globals.
`esphome-hems.yaml` only holds the hardware platform config and cross-cutting application glue.

---

## Beginner guide: §14a control in 3 steps

### Step 1 — Create credentials

```bash
cp secrets.example.yaml secrets.yaml
```

Open `secrets.yaml` and fill in your values:

```yaml
api_encryption_key: "..."          # ESPHome API key (generate randomly)
ota_password: "..."                # OTA password
fronius_ip: "192.168.x.x"         # Fronius IP (or leave empty)
mqtt_broker: "192.168.x.x"        # MQTT broker IP
hems_ip: "192.168.x.x"            # IP of this HEMS device (for OTA upload)

# EEBus pairing: leave empty to pair via web UI
eebus_cs_remote_ski: ""            # CLS Steuerbox of VNB (set during pairing)
eebus_eg1_remote_ski: ""           # EG1 device — heat pump (set during pairing)
eebus_eg2_remote_ski: ""           # EG2 device — wallbox (set during pairing)
```

> **Note:** `secrets.yaml` is never committed — only `secrets.example.yaml` is in the repository.

---

### Step 2 — Compile and flash

```powershell
# Compile only:
.\compile.ps1

# Compile + upload via OTA (reads hems_ip from secrets.yaml):
.\compile.ps1 --upload

# Explicit target:
.\compile.ps1 --upload --device 192.168.x.x
```

> Do **not** use `esphome run` directly — the script ensures both build cache paths are in sync.

---

### Step 3 — EEBus pairing

Pairing must be done **once** for each connection.

#### CLS Steuerbox (VNB gateway → HEMS)

1. Open ESPHome web UI → press **"CS Pairing-Modus starten"** (5-minute window)
2. Trigger pairing on the VNB gateway / CLS device
3. The device SKI appears under "CS Pairing ausstehend" → press **"CS Pairing akzeptieren"**
4. Status changes to "Verbunden" — SKI is stored in NVS and persists across reboots

#### Heat pump / wallbox (HEMS → StVE)

1. ESPHome web UI → press **"EG1 Pairing-Modus starten"**
2. Activate EEBus pairing on the device (e.g. Bosch app or heat pump display)
3. Connection is established automatically — status changes to "Verbunden"

> **Tip:** After successful pairing the SKI is stored in NVS. On the next boot the controller reconnects automatically without re-pairing.

---

## YAML reference: EEBus components

### `eebus_cs` — CS receiver (VNB → HEMS)

Receives §14a power limits from the VNB gateway.

```yaml
eebus_cs:
  id: hems_cs
  remote_ski: !secret eebus_cs_remote_ski
  device_brand: "DIY"
  device_type: "HEMS"
  device_model: "esphome-hems"

  on_limit_active:                 # Called when VNB sends a limit
    - lambda: |-
        float limit = x;           # x = power limit in watts
        id(eg1).set_limit(limit);  # Forward limit to heat pump

  on_limit_cleared:                # Called when limit is lifted
    - lambda: |-
        id(eg1).clear_limit();
```

**Methods available in lambdas:**

| Method | Returns | Description |
|--------|---------|-------------|
| `id(hems_cs).is_limit_active()` | `bool` | Is a limit currently active? |
| `id(hems_cs).current_limit_w()` | `float` | Current limit in watts |
| `id(hems_cs).is_connected()` | `bool` | Connection to CLS Steuerbox active? |
| `id(hems_cs).local_ski()` | `std::string` | Own SKI (for handover to VNB) |
| `id(hems_cs).paired_remote_ski()` | `std::string` | SKI of paired CLS Steuerbox |

---

### `eebus_eg` — EG sender (HEMS → StVE)

Sends §14a power limits to EEBus-capable devices (heat pump, wallbox).

```yaml
eebus_eg:
  id: eg1                          # Instance name — choose freely (eg1, eg_wp, eg_wb, ...)
  ship_port: 4713                  # SHIP port (unique per StVE; default 4713)
  remote_ski: !secret eebus_eg1_remote_ski
  instance_name: "WP"              # Label shown in logs and web UI
  device_brand: "DIY"
  device_type: "HEMS"
  device_model: "esphome-hems"
  failsafe_limit_w: 2600           # Limit the device holds if connection drops (default 4200)
  failsafe_duration_s: 7200        # Duration of failsafe in seconds (default 7200 = 2 h)

  on_eg_connected:                 # Called when StVE connects
    - lambda: |-
        if (id(hems_cs).is_limit_active())
          id(eg1).set_limit(id(hems_cs).current_limit_w());

  on_eg_disconnected:              # Called when StVE disconnects
    - lambda: |-
        ESP_LOGW("eebus", "Heat pump disconnected");

  on_power_reading:                # Called on new MPC power measurement
    - lambda: |-
        float watt = x;            # x = measured power in watts
```

**Methods available in lambdas:**

| Method | Parameter | Description |
|--------|-----------|-------------|
| `id(eg1).set_limit(watts)` | `float` | Set power limit (min. 4,200 W) |
| `id(eg1).clear_limit()` | — | Remove limit (full power) |
| `id(eg1).current_power_w()` | — | Last MPC power reading in watts |
| `id(eg1).active_limit_w()` | — | Last acknowledged limit in watts |
| `id(eg1).is_connected()` | — | Connection to StVE active? |
| `id(eg1).update_failsafe_limit_w(w)` | `float` | Change failsafe limit at runtime |
| `id(eg1).update_failsafe_duration_s(s)` | `uint32_t` | Change failsafe duration at runtime |

---

### Multiple StVE

Add one `eebus_eg` instance per controllable device, each with its own port:

```yaml
eebus_eg:
  - id: hems_eg1
    ship_port: 4713
    instance_name: "EG1"
    remote_ski: !secret eebus_eg1_remote_ski
    failsafe_limit_w: 4200
    failsafe_duration_s: 7200

  - id: hems_eg2
    ship_port: 4714
    instance_name: "EG2"
    remote_ski: !secret eebus_eg2_remote_ski
    failsafe_limit_w: 4200
    failsafe_duration_s: 7200
```

**Plugin pattern**: each EG package file adds its own `eebus_cs: on_limit_active:` block.
ESPHome concatenates trigger lists across packages — the CS does not need to enumerate EGs.

```yaml
# In eebus-eg-1.yaml — forward CS limit to EG1
eebus_cs:
  on_limit_active:
    - lambda: "id(hems_eg1).set_limit(x);"
  on_limit_cleared:
    - lambda: "id(hems_eg1).clear_limit();"
```

Adding EG3 means creating a new `eebus-eg-3.yaml` and adding `id: hems_eg3` —
no changes to existing files.

> **§14a note:** The VNB sends at least 4,200 W per registered StVE. Internal distribution is up to the HEMS operator. Limits below 4,200 W are not acknowledged by EEBus devices.

---

## EEBus use cases

| Use case | Short | Direction | Description |
|----------|-------|-----------|-------------|
| LimitationOfPowerConsumption | LPC | VNB→HEMS→StVE | §14a power limit |
| MonitoringOfPowerConsumption | MPC | StVE→HEMS | Power feedback |
| OptimizationOfSelfConsumptionByHPCF | OSSHPCF | StVE→HEMS | Compressor flexibility (PV) |

**OSSHPCF/SEMP** is detected automatically when the heat pump announces its compressor entity. The controller then subscribes to the SEMP data points (`fn=103`) and logs incoming compressor schedules — the basis for future PV self-consumption optimisation.

---

## Failsafe behaviour

When the connection between HEMS and StVE drops, the StVE falls back to its failsafe limit:

- `failsafe_limit_w`: Power the device holds itself (e.g. 2,600 W)
- `failsafe_duration_s`: How long the failsafe applies (then: full power)

Both values can be changed at runtime via the web UI without restarting.

The failsafe test (web UI → "EG1 Failsafe testen") stops the heartbeat for 120 s and verifies that the device correctly switches to its failsafe value.

---

## Diagnostic scripts

The `tools/diagnostics/` directory contains scripts for testing and debugging.

### `check_eebus.py` — EEBus port and TLS check

Tests TCP connectivity, TLS handshake, SKI extraction, mDNS, and SHIP CMI handshake against the HEMS ports.

```powershell
python tools/diagnostics/check_eebus.py
```

### `fake_steuerbox.py` — Simulated CLS Steuerbox

Simulates a CLS Steuerbox (VNB gateway) connecting to the HEMS via EEBus SHIP/SPINE. Useful for testing LPC limit reception without a real VNB device.

The script uses `.fake_steuerbox_cert.pem` / `.fake_steuerbox_key.pem` (self-signed, generated on first run) to perform the TLS handshake. The resulting SKI must be paired in the HEMS web UI once (CS pairing flow).

```powershell
# Install dependencies first:
python -m pip install -r requirements-dev.txt

# Run:
python tools/diagnostics/fake_steuerbox.py --host 192.168.x.x
```

### `read_fronius.py` — Fronius Modbus TCP diagnostics

```powershell
$env:FRONIUS_HOST = "192.168.x.x"
python tools/diagnostics/read_fronius.py
```

---

## Security

`secrets.yaml`, ESPHome build output, virtual environments, and private documentation are not committed. The repository contains only `secrets.example.yaml` with empty values.

---

## Credits

| Component | Author / Project | License |
|-----------|-----------------|---------|
| **openeebus** — SHIP/SPINE C library | [NIBE AB](https://github.com/nibe-stefan/openeebus) | Apache 2.0 |
| **esphome_modbus_tcp** — Modbus TCP server | [creepystefan](https://github.com/creepystefan/esphome_modbus_tcp) | see repo |
| **xemex-csmb** — Modbus RTU server emulation | [thomase1234](https://github.com/thomase1234/esphome-fake-xemex-csmb) | see repo |
| **eebus_cs / eebus_eg** — EEBus ESPHome components | bgewehr (own development) | MIT |

---

## License

MIT License. See `LICENSE`.
