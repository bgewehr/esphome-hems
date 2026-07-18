"""Capture EV meter and CSMB currents around an EV power-limit change."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


ENTITIES = {
    "setpoint_w": "number/esphome_hems_ev_leistungsbegrenzung",
    "ev_power_w": "sensor/esphome_hems_fronius_ev_power",
    "ev_current_a": "sensor/esphome_hems_fronius_ev_current_a",
    "ev_current_b": "sensor/esphome_hems_fronius_ev_current_b",
    "ev_current_c": "sensor/esphome_hems_fronius_ev_current_c",
    "csmb_current_a": "number/esphome_hems_ct1_current",
    "csmb_current_b": "number/esphome_hems_ct2_current",
    "csmb_current_c": "number/esphome_hems_ct3_current",
}


def read_value(host: str, entity: str, timeout: float) -> float:
    cache_buster = time.time_ns()
    url = f"http://{host}/{entity}?_={cache_buster}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.load(response)
    return float(payload["value"])


def set_limit(host: str, value: float, timeout: float) -> None:
    encoded_value = urllib.parse.quote(str(value))
    url = (
        f"http://{host}/number/esphome_hems_ev_leistungsbegrenzung/set"
        f"?value={encoded_value}"
    )
    request = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(request, timeout=timeout):
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.178.24")
    parser.add_argument("--baseline-duration", type=float, default=180.0)
    parser.add_argument("--response-duration", type=float, default=180.0)
    parser.add_argument("--setpoint", type=float, default=6000.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or Path("private/captures") / f"ev-response-{timestamp}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    change_at = started + args.baseline_duration
    finish_at = change_at + args.response_duration
    baseline_setpoint = read_value(args.host, ENTITIES["setpoint_w"], args.timeout)
    next_sample = started
    changed = False
    sample_count = 0
    error_count = 0

    with output.open("w", newline="", encoding="utf-8") as output_file:
        fieldnames = ["elapsed_s", "phase", *ENTITIES, "errors"]
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()

        while time.monotonic() < finish_at:
            now = time.monotonic()
            if not changed and now >= change_at:
                set_limit(args.host, args.setpoint, args.timeout)
                changed = True
                print(f"{now - started:.3f}s: set EV limit to {args.setpoint:.0f} W", flush=True)

            row: dict[str, str | float] = {
                "elapsed_s": f"{now - started:.3f}",
                "phase": "response" if changed else "baseline",
                "errors": "",
            }
            errors = []
            for name, entity in ENTITIES.items():
                try:
                    row[name] = read_value(args.host, entity, args.timeout)
                except Exception as error:
                    row[name] = ""
                    errors.append(f"{name}: {error}")
            if not changed and row["setpoint_w"] != baseline_setpoint:
                raise RuntimeError(
                    "EV setpoint changed during baseline; another controller or "
                    "capture is active"
                )
            row["errors"] = " | ".join(errors)
            error_count += len(errors)
            writer.writerow(row)
            output_file.flush()
            sample_count += 1

            next_sample += args.interval
            delay = next_sample - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    print(f"Captured {sample_count} samples with {error_count} read errors: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())