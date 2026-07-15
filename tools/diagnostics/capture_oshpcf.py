"""Passively capture OSSHPCF and heat-pump activity for a bounded period."""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import subprocess
import sys
import time


RELEVANT_LINE = re.compile(
    r"eebus_eg|OSSHPCF|Semp|SmartEnergy|Fronius HP Power|EG1 MPC Leistung|"
    r"Connecting to|Successfully connected|Connection error|Disconnected",
    re.IGNORECASE,
)
HP_POWER = re.compile(r"Fronius HP Power' >> ([0-9.]+) W")
MPC_POWER = re.compile(r"EG1 MPC Leistung' >> ([0-9.]+) W")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.178.24")
    parser.add_argument("--config", default="esphome-hems.yaml")
    parser.add_argument("--duration", type=int, default=24 * 60 * 60)
    parser.add_argument("--output-dir", default="private/captures")
    return parser.parse_args()


def update_maximum(line: str, pattern: re.Pattern[str], current: float) -> float:
    match = pattern.search(line)
    return max(current, float(match.group(1))) if match else current


def main() -> int:
    args = parse_args()
    repository_root = pathlib.Path(__file__).resolve().parents[2]
    output_dir = pathlib.Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = repository_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.datetime.now().astimezone()
    output_path = output_dir / f"oshpcf-{started:%Y%m%d-%H%M%S}.log"
    deadline = time.monotonic() + args.duration
    semp_messages = 0
    subscriptions = 0
    reconnects = 0
    max_hp_power = 0.0
    max_mpc_power = 0.0

    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        header = f"Capture started {started.isoformat()} for {args.duration} seconds\n"
        output.write(header)
        print(header, end="")

        while time.monotonic() < deadline:
            command = ["esphome", "logs", args.config, "--device", args.host]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    if time.monotonic() >= deadline:
                        break
                    if not RELEVANT_LINE.search(line):
                        continue
                    output.write(line)
                    output.flush()
                    sys.stdout.write(line)
                    semp_messages += "SempData" in line or "OSSHPCF data:" in line
                    subscriptions += "subscribed to remote SEMP" in line
                    max_hp_power = update_maximum(line, HP_POWER, max_hp_power)
                    max_mpc_power = update_maximum(line, MPC_POWER, max_mpc_power)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

            if time.monotonic() < deadline:
                reconnects += 1
                time.sleep(min(5, max(0, deadline - time.monotonic())))

        finished = datetime.datetime.now().astimezone()
        summary = (
            f"Capture finished {finished.isoformat()}; SEMP messages={semp_messages}; "
            f"subscriptions={subscriptions}; logger reconnects={reconnects}; "
            f"max HP power={max_hp_power:.0f} W; max MPC power={max_mpc_power:.0f} W\n"
        )
        output.write(summary)
        print(summary, end="")

    print(f"Capture written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())