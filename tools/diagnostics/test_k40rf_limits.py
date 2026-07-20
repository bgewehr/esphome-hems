"""Probe K40RF acceptance of LPC limits below 4.2 kW via ESPHome web API."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


HOST_DEFAULT = "192.168.178.24"
TEST_MODE = "switch/esphome_hems_eg1_untergrenzen-testmodus"
TEST_LIMIT = "number/esphome_hems_eg1_test_leistungslimit"
ACK = "binary_sensor/esphome_hems_eg1_testlimit_best__tigt"
ACTIVE_LIMIT = "sensor/esphome_hems_eg1___14a_leistungslimit"
MPC_POWER = "sensor/esphome_hems_eg1_mpc_leistung"
FRONIUS_HP_POWER = "sensor/esphome_hems_fronius_hp_power"
CONNECTION = "text_sensor/esphome_hems_eg1_verbindungsstatus"
VNB_LIMIT = "sensor/esphome_hems___14a_leistungslimit"


def read_entity(host: str, entity: str, timeout: float) -> dict:
    with urllib.request.urlopen(
        f"http://{host}/{entity}?_={time.time_ns()}", timeout=timeout
    ) as response:
        return json.load(response)


def post_action(host: str, path: str, timeout: float) -> None:
    request = urllib.request.Request(f"http://{host}/{path}", method="POST")
    with urllib.request.urlopen(request, timeout=timeout):
        pass


def set_number(host: str, entity: str, value: float, timeout: float) -> None:
    encoded = urllib.parse.quote(str(value))
    post_action(host, f"{entity}/set?value={encoded}", timeout)


def value(host: str, entity: str, timeout: float):
    return read_entity(host, entity, timeout).get("value")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=HOST_DEFAULT)
    parser.add_argument(
        "--limits", type=float, nargs="+", default=[4000, 3600, 3200, 2800, 2400, 2000]
    )
    parser.add_argument("--hold", type=float, default=15.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output = args.output or Path("private/captures") / (
        f"k40rf-low-limits-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    connection = str(value(args.host, CONNECTION, args.timeout))
    if connection != "Verbunden":
        raise RuntimeError(f"EG1 is not connected: {connection}")
    if value(args.host, VNB_LIMIT, args.timeout) is not None:
        raise RuntimeError("VNB limit is active; refusing direct K40RF test")

    rows: list[dict[str, object]] = []
    summaries: list[tuple[float, bool, float | None]] = []

    try:
        post_action(args.host, f"{TEST_MODE}/turn_on", args.timeout)
        for requested_w in args.limits:
            set_number(args.host, TEST_LIMIT, requested_w, args.timeout)
            started = time.monotonic()
            while time.monotonic() - started < args.hold:
                row = {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "requested_w": requested_w,
                    "elapsed_s": round(time.monotonic() - started, 3),
                    "ack": bool(value(args.host, ACK, args.timeout)),
                    "active_limit_w": value(args.host, ACTIVE_LIMIT, args.timeout),
                    "mpc_power_w": value(args.host, MPC_POWER, args.timeout),
                    "fronius_hp_power_w": value(args.host, FRONIUS_HP_POWER, args.timeout),
                    "connection": value(args.host, CONNECTION, args.timeout),
                }
                rows.append(row)
                delay = args.interval - (time.monotonic() - started - row["elapsed_s"])
                if delay > 0:
                    time.sleep(delay)
            final = [row for row in rows if row["requested_w"] == requested_w][-3:]
            accepted = all(row["ack"] for row in final)
            active_values = [
                float(row["active_limit_w"])
                for row in final
                if row["active_limit_w"] is not None
            ]
            active_w = sum(active_values) / len(active_values) if active_values else None
            summaries.append((requested_w, accepted, active_w))
            print(
                f"{requested_w:.0f} W: ACK={'yes' if accepted else 'no'}, "
                f"active={active_w if active_w is not None else 'NA'} W",
                flush=True,
            )
    finally:
        try:
            set_number(args.host, TEST_LIMIT, 4200, args.timeout)
            time.sleep(3)
        finally:
            post_action(args.host, f"{TEST_MODE}/turn_off", args.timeout)

    with output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Capture: {output}")
    return 0 if all(accepted for _, accepted, _ in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())