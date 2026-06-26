# esphome-hems

ESPHome configuration for a Waveshare ESP32-S3-POE-ETH-8DI-8RO based home energy management controller.

The setup combines:

- 8 relay outputs and digital inputs on the Waveshare board
- Xemex CSMB-style Modbus RTU server emulation
- Fronius Gen24 Modbus TCP monitoring and battery discharge limiting
- EEBUS LPC bridge integration for controllable loads
- S0 water meter pulse counting

## Repository Layout

```text
.
├── esphome-hems.yaml             # Main ESPHome node configuration
├── fronius-gen24.yaml            # Fronius package included by the main config
├── custom_components/            # Local ESPHome components used by this config
├── tools/diagnostics/            # Optional local diagnostic scripts
├── secrets.example.yaml          # Empty template for local secrets
└── private/                      # Local-only material, ignored by git
```

## Setup

1. Copy `secrets.example.yaml` to `secrets.yaml`.
2. Fill in your local API key, OTA password, IP addresses, MQTT broker and EEBUS SKI.
3. Validate or build with ESPHome:

```powershell
esphome config esphome-hems.yaml
esphome run esphome-hems.yaml
```

## Diagnostic Scripts

The scripts in `tools/diagnostics` read connection settings from environment variables:

- `FRONIUS_HOST`, optional `FRONIUS_PORT`
- `EEBUS_BRIDGE_HOST`, optional `EEBUS_BRIDGE_PORT`
- `EEBUS_DEVICE_SKI`

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
