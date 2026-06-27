# esphome-hems

ESPHome configuration for a Waveshare ESP32-S3-POE-ETH-8DI-8RO based home energy management controller.

The setup combines:

- 8 relay outputs and digital inputs on the Waveshare board
- Xemex CSMB-style Modbus RTU server emulation
- Fronius Gen24 Modbus TCP monitoring and battery discharge limiting
- Native OpenEEBUS SHIP/SPINE integration for CLS-Steuerbox LPC and EEBus heat-pump (EG/LPC) control
- S0 water meter pulse counting

## Repository Layout

```text
.
├── esphome-hems.yaml             # Main ESPHome node configuration
├── xemex-csmb.yaml              # Xemex CSMB Modbus RTU server package
├── fronius-gen24.yaml            # Fronius package included by the main config
├── eebus-openeebus.yaml          # Native OpenEEBUS LPC/WP package
├── custom_components/            # Local ESPHome components used by this config
├── tools/diagnostics/            # Optional local Fronius diagnostic scripts
├── secrets.example.yaml          # Empty template for local secrets
└── private/                      # Local-only material, ignored by git
```

## Setup

1. Copy `secrets.example.yaml` to `secrets.yaml`.
2. Fill in your local API key, OTA password, Fronius IP, MQTT broker and optional OpenEEBUS SKIs.
3. Leave `eebus_cls_remote_ski` and `eebus_wp_remote_ski` empty for pairing mode / auto-discovery.
4. Validate or build with ESPHome:

```powershell
esphome config esphome-hems.yaml
esphome run esphome-hems.yaml
```

## OpenEEBUS Use Cases

The native OpenEEBUS package covers the use cases exposed by `bgewehr/openeebus-esphome`:

- `eebus_lpc`: LPC CS role, receives power limits from a CLS-Steuerbox.
- `eebus_wp`: LPC EG role, sends power limits to an EEBus-capable heat-pump gateway.
- MPC: reads heat-pump power via EEBus.
- OHPCF: represented through the OpenEEBUS heat-pump optimisation path exposed by the component.

Pairing and diagnostics are exposed through the ESPHome web server.

## Diagnostic Scripts

The scripts in `tools/diagnostics` read Fronius connection settings from environment variables:

- `FRONIUS_HOST`, optional `FRONIUS_PORT`

Install optional script dependencies with:

```powershell
python -m pip install -r requirements-dev.txt
```

Example:

```powershell
$env:FRONIUS_HOST = "192.168.1.50"
python tools/diagnostics/read_fronius.py
```

## Security

Do not commit `secrets.yaml`, ESPHome build output, virtual environments, cache files or private documentation. The repository includes only `secrets.example.yaml` with empty values.

## License

MIT License. See `LICENSE`.