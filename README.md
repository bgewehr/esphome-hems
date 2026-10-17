# esphome-hems

ESPHome configuration for a HEMS controller based on the **Waveshare ESP32-S3-POE-ETH-8DI-8RO**.

The controller receives §14a EnWG power limits from the grid operator (VNB) via EEBus SHIP/SPINE and distributes them internally to controllable consumption units (StVE) such as heat pumps and wallboxes — also via EEBus.

## Work in progress and disclaimer

**This project is a work in progress (WIP). Use it entirely at your own risk.**
It is experimental, may contain bugs or incomplete features, and is not a
certified or production-ready energy management system. Incorrect operation
may disrupt connected equipment, cause damage or result in noncompliance with
grid-operator requirements.

The software and documentation are provided **"as is", without warranty of any
kind**, express or implied, including fitness for a particular purpose. To the
extent permitted by applicable law, the authors and contributors accept no
liability for claims, damage or losses arising from its use. See the [MIT
License](LICENSE) for the full terms.

You are responsible for validating suitability, safe operation and compliance
for your installation. Test on isolated equipment first and have electrical
work and required protective measures assessed by a qualified professional.

## Start here

This repository is the current ESPHome integration. The earlier `eebus_wp`
component in `bgewehr/openeebus@bg-hems` is a historical development version,
not a standalone installation example. Use `eebus_cs` to receive limits from a
CLS gateway and `eebus_eg` to send limits to a heat pump or another device.
The OpenEEBus library fork is a dependency, not the ESPHome entry point.

The reference hardware is **Waveshare ESP32-S3-POE-ETH-8DI-8RO**, with **16 MB
flash and 8 MB octal PSRAM**, using **ESP-IDF**. CI compiles with **ESPHome
2026.5.3** and Python 3.13. Newer ESPHome versions, including 2026.9.1, and other
boards such as `esp32-poe-iso` are not verified by that CI configuration.
Changing only the board name does not adapt Ethernet pins, memory or peripherals.

### Get the complete source

```bash
git clone --recurse-submodules https://github.com/bgewehr/esphome-hems.git
cd esphome-hems
```

For an existing Git checkout:

```bash
git submodule update --init --recursive
```

Keep `components/`, `openeebus/` and `port/` together. A GitHub ZIP download does
not include the submodule contents; copying only `components/` is insufficient.
The supported starting point is a complete local checkout, not an isolated
`external_components: github://...` snippet. The build integration in `eebus_cs`
also supplies the OpenEEBus C sources and ESP-IDF dependencies used by `eebus_eg`.

The full configuration below is installation-specific. Review the hardware,
connected devices and control behavior before flashing; successful compilation
does not establish suitability or regulatory compliance for another installation.

### Minimal EEBus example

Start with [eebus-minimal.yaml](eebus-minimal.yaml) on the reference Waveshare
board. It includes Ethernet (DHCP), time synchronization, a local web interface,
both EEBus roles, pairing buttons, SKI displays and received-limit/power sensors.
It needs no credentials, Home Assistant, MQTT, inverter, meter or relay wiring.
Its build directory is separate from the full installation's firmware.

Use Python 3.13 in a dedicated environment and install the CI version:

```bash
python -m pip install esphome==2026.5.3
esphome config eebus-minimal.yaml
esphome compile eebus-minimal.yaml
```

Alternatively, with Docker running, execute from the repository root:

```bash
docker run --rm -v "${PWD}:/config" -v hems-onboarding-build:/config/.esphome ghcr.io/esphome/esphome:2026.5.3 compile eebus-minimal.yaml
```

The named volume keeps the toolchain and build cache on Linux storage, avoiding
slow package extraction on Windows bind mounts. With this command the generated
firmware also lives in that volume, not in the host's `.esphome` directory. For
example, copy the factory image to the current host directory after compilation:

```bash
docker run --rm -v "${PWD}:/out" -v hems-onboarding-build:/data alpine:3.22 cp /data/build/eebus-minimal/.pioenvs/eebus-minimal/firmware.factory.bin /out/eebus-minimal.factory.bin
```

The example has no OTA or Home Assistant API. For initial installation, upload
over USB using `esphome upload eebus-minimal.yaml --device <serial-port>` after a
native compile, or flash the exported Docker factory image with ESPHome Web.
Use a spare/test controller, not a controller responsible for live power limits.
Open its DHCP address in a browser. For a CLS gateway, start CS pairing, check
the pending SKI and explicitly accept it. For a heat pump, start EG1 pairing and
activate pairing at the remote device. The Forget buttons remove existing trust.

**Lab use only:** the web interface is unauthenticated; use a trusted, isolated
network and do not expose it to the Internet. There is no automatic forwarding
of CS limits to EG1, no manual limit control and no relay fallback. This is a
build/pairing example, not a complete Section 14a controller. Pairing is **not
read-only**: the EG component negotiates failsafe settings (4200 W, 7200 seconds
in this example) and participates in the device's control protocol. Review those
settings and the device documentation before pairing. CI checks compilation,
not physical operation or acceptance on other hardware.

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
- **OSSHPCF/SEMP**: read-only monitoring and diagnostic reads of compressor flexibility
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

## Reference installation: §14a control in 3 steps

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

### Local diagnostics and private files

Public diagnostic scripts use generic hostnames such as `esphome-hems.local`
and `k40.local`. Supply your device address with the script's host argument.
Fronius probes use `FRONIUS_HOST` and `FRONIUS_PORT` environment variables,
defaulting to `fronius.local:502`.

Keep private scripts and exports under `private/`. Local VS Code tasks,
the local K40 PowerShell script, `.env` files, and private key/certificate
files are ignored. Environment files are not loaded automatically by the tools.
Git ignore rules match paths, not file contents; they do not detect new
credentials or remove sensitive data from earlier commits.

---

### Step 2 — Compile and flash

```powershell
# Compile only:
.\compile.ps1

# Compile + upload via OTA (reads hems_ip from secrets.yaml):
.\compile.ps1 --upload

# Explicit target:
.\compile.ps1 --upload --device 192.168.x.x

# Clean build + upload:
.\compile.ps1 --clean --upload --device 192.168.x.x
```

The script resolves ESPHome from `PATH` or its standard Windows install locations
and prints explicit start, completion, duration, and exit status for every stage.
Do **not** use `esphome run` directly; compile and upload stay separate so OTA
can use the artifact that was just validated. Do not overlap builds in the
same build directory, and verify the live Build Time after deployment.

On Linux, use the ESPHome CLI with the same pinned version as CI. The reference
YAML contains a Windows-specific `build_path`; change it to a relative path such
as `build/esphome-hems` first (resolved under ESPHome's data directory). Compile
and upload separately:

```bash
esphome compile esphome-hems.yaml
esphome upload esphome-hems.yaml --device <device-address>
```

### Build troubleshooting

| Symptom | Check |
| --- | --- |
| `src/service/api/eebus_service_interface.h: No such file or directory` | Use the current integration and a complete recursive checkout. Confirm `openeebus/src/service/api/eebus_service_interface.h` exists. The old `eebus_wp` build hook does not integrate the library; adding one include path alone is not a complete fix. |
| Missing OpenEEBus sources after downloading a ZIP | Clone with `--recurse-submodules`, or initialize submodules in an existing Git checkout. |
| Undefined references after changing library sources | Clean the selected configuration with `esphome clean <configuration.yaml>`, then compile again. Cached CMake source discovery can miss newly generated source wrappers. |
| YAML validates but compilation fails | Validation checks configuration schemas, not whether all C/C++ dependencies compile and link. Read the first compiler error. |
| Errors with a different ESPHome release | Reproduce with ESPHome 2026.5.3 before investigating version compatibility. Include the version, board, configuration and first compiler error in a report; remove credentials. |

### Local validation tasks

The reference installation has these local tasks under **Terminal → Run Task**
in VS Code. Its task file is ignored because it contains installation-specific
addresses; a fresh checkout does not include these task definitions:

- `Validate local environment` checks Git, Docker client/daemon, Python, and ESPHome.
- `Validate ESPHome firmware` compiles without uploading.
- `Run host tests in Docker` runs the root CMake/CTest suite.
- `Validate OpenEEBus in Docker` builds and tests OpenEEBus with GCC 14 on Debian Trixie.
- `Upload ESPHome firmware OTA` uploads the existing validated artifact without rebuilding it.

The local tasks use clean, noninteractive PowerShell processes and checked-in tool
resolvers, so they do not depend on profile initialization or a particular
integrated-terminal session. Long-running tasks end with an explicit exit status.

Without that local task file, use the ESPHome commands above and the standard
host-test commands (requires a C++ compiler and CMake):

```bash
cmake -S tests -B build/host-tests
cmake --build build/host-tests --config Release
ctest --test-dir build/host-tests -C Release --output-on-failure
python tools/diagnostics/test_discover_one_wire.py
python tools/diagnostics/test_capture_oshpcf.py
```

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

The following callback example is for one downstream device only. The full
installation uses the central budget distributor instead; do not combine
direct forwarding with that distributor or duplicate it for multiple devices.

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
  failsafe_limit_w: 4200           # Device fallback setting; verify against the device documentation
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
| `id(eg1).set_limit(watts)` | `float` | Set active limit; a configured per-device minimum may clamp it. Zero clears the limit. |
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

**Central distribution:** `netzdienliche-steuerung.yaml` allocates the aggregate
CS budget between EG1, the local EV path and EG2. Do not forward the complete
CS limit independently to each device. Adding another EG also requires updating
the allocator, operator parameters and validation; a new package alone is not enough.

The Bosch K40RF tests support a 4200 W active-limit floor for this installation,
not a universal EEBus minimum. The local wallbox has separate charging-current
constraints. See [budget distribution](docs/power-distribution-concept.md) for
the implementation and unresolved hardware-compliance limits.

---

## EEBus use cases

| Use case | Short | Direction | Description |
|----------|-------|-----------|-------------|
| LimitationOfPowerConsumption | LPC | VNB→HEMS→StVE | §14a power limit |
| MonitoringOfPowerConsumption | MPC | StVE→HEMS | Power feedback |
| OptimizationOfSelfConsumptionByHPCF | OSSHPCF | StVE→HEMS | Compressor flexibility (PV) |

**OSSHPCF/SEMP** is detected automatically when the heat pump announces its compressor entity. The controller then subscribes to the remote `SmartEnergyManagementPs` server and observes `smartEnergyManagementPsData` plus the related configuration and price functions — the basis for future PV self-consumption optimisation.

The current implementation is deliberately read-only: the exact sequence,
slot and constraint data exposed by the Bosch gateway still needs to be
captured and documented before control requests are enabled. See
[Bosch heat pump OSSHPCF / SEMP](docs/oss-hpcf-bosch.md) for the known SPINE
contract, open Bosch-specific questions and the staged implementation plan.

For a bounded, start-triggered observation on the full reference firmware:

```bash
python -u tools/diagnostics/capture_oshpcf.py --host <device-address> --duration 86400 --start-reads
```

This mode presses only the existing SEMP read button: ten attempts at 60-second
intervals following an external HP-meter transition from <=50 W to >50 W.
It does not start heating or select a sequence. Keep the recording machine
awake; raw captures contain private identifiers and stay under `private/captures/`.
Busy reads or connection/data gaps can reduce the number of completed replies.
Omit `--start-reads` for passive logging.

The project-wide target architecture and phased roadmap are documented in
[System architecture](docs/system-architecture.md). Work status and execution
order are tracked in the central [development TODO](TODO.md).

---

## Failsafe behaviour

The EG negotiates fallback parameters with the StVE. Heartbeat loss and the
device's own timeout handling determine when they take effect:

- `failsafe_limit_w`: Configured fallback power (4200 W in the reference setup).
- `failsafe_duration_s`: Configured duration parameter (7200 seconds in the reference setup).

Both values can be changed at runtime via the web UI without restarting.

The failsafe test (web UI → "EG1 Failsafe testen") suppresses heartbeats for
120 seconds. It changes live protocol behavior; it is not a read-only check.
Measure the actual device response separately. Pressing the button does not
by itself verify fallback, recovery or physical compliance.

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
python tools/fake_steuerbox.py <device-address>
```

### `read_fronius.py` — Fronius Modbus TCP diagnostics

```powershell
$env:FRONIUS_HOST = "192.168.x.x"
python tools/diagnostics/read_fronius.py
```

---

## Security

Local secrets, build output, virtual environments and private captures are
excluded by ignore rules. `secrets.example.yaml` contains placeholders, not
usable credentials. This is not a historical-secret audit: ignore rules do
not remove past commits. Do not expose the reference web or Modbus interfaces
to untrusted networks; access hardening remains an open project task.

---

## Credits

| Component | Author / Project | License |
|-----------|-----------------|---------|
| **openeebus** — SHIP/SPINE C library | [NIBE AB](https://github.com/NIBEGroup/openeebus) | Apache 2.0 |
| **esphome_modbus_tcp** — Modbus TCP server | [creepystefan](https://github.com/creepystefan/esphome_modbus_tcp) | see repo |
| **xemex-csmb** — Modbus RTU server emulation | [thomase1234](https://github.com/thomase1234/esphome-fake-xemex-csmb) | see repo |
| **eebus_cs / eebus_eg** — EEBus ESPHome components | bgewehr (own development) | MIT |

---

## License

MIT License. See `LICENSE`.
