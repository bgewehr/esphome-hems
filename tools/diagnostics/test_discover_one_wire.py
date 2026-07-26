"""Focused tests for reliable 1-Wire probe registration."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from discover_one_wire import (  # noqa: E402
    assign_missing_names,
    load_names,
    parse_ds18b20_addresses,
    render_names_json,
    render_sensor_yaml,
    write_if_changed,
)


class DiscoverOneWireTest(unittest.TestCase):
    def test_parses_unique_ds18b20_addresses_in_stable_order(self) -> None:
        lines = [
            "[C][ds2484.onewire]: 0xAABBCCDDEEFF0028 (DS18B20)",
            "\x1b[0;32m[C][ds2484.onewire]: 0x1234567812345628 (DS18B20)\x1b[0m",
            "[C][ds2484.onewire]: 0xAABBCCDDEEFF0028 (DS18B20)",
            "[C][ds2484.onewire]: 0x1234567812345610 (DS18S20)",
            "[W][one_wire]: Dallas device 0x0000000000000028 has invalid CRC.",
        ]

        self.assertEqual(
            parse_ds18b20_addresses(lines),
            [0x1234567812345628, 0xAABBCCDDEEFF0028],
        )

    def test_prompts_only_for_new_addresses(self) -> None:
        answers = iter(["Flow temperature"])
        names = assign_missing_names(
            [0x1234567812345628, 0xAABBCCDDEEFF0028],
            {0x1234567812345628: "Return temperature"},
            lambda _: next(answers),
        )

        self.assertEqual(names[0x1234567812345628], "Return temperature")
        self.assertEqual(names[0xAABBCCDDEEFF0028], "Flow temperature")

    def test_renders_address_bound_speaking_name(self) -> None:
        address = 0xAABBCCDDEEFF0028
        rendered = render_sensor_yaml([address], {address: 'Tank "top"'})

        self.assertIn("address: 0xAABBCCDDEEFF0028", rendered)
        self.assertIn('${device_name} Tank \\"top\\"', rendered)
        self.assertIn("filter_out: 85.0", rendered)
        self.assertIn("x > 125.0f", rendered)
        self.assertIn("return NAN", rendered)
        self.assertIn("timeout: 180s", rendered)
        self.assertIn("sorting_group_id: sg_one_wire", rendered)
        self.assertNotIn("index:", rendered)

    def test_name_registry_round_trip(self) -> None:
        address = 0xAABBCCDDEEFF0028
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "names.json"
            path.write_text(
                render_names_json({address: "Buffer top"}), encoding="utf-8"
            )

            self.assertEqual(load_names(path), {address: "Buffer top"})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"0xAABBCCDDEEFF0028": "Buffer top"},
            )

    def test_atomic_write_skips_unchanged_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "sensors.yaml"
            self.assertTrue(write_if_changed(path, "[]\n"))
            self.assertFalse(write_if_changed(path, "[]\n"))


if __name__ == "__main__":
    unittest.main()