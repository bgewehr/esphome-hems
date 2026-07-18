"""Evaluate an EV response capture against the Xemex control criteria."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--target", type=float, default=6000.0)
    parser.add_argument("--tolerance", type=float, default=500.0)
    parser.add_argument("--settling-time", type=float, default=60.0)
    parser.add_argument("--settled-samples", type=int, default=5)
    parser.add_argument("--final-window", type=float, default=120.0)
    parser.add_argument("--required-in-band", type=float, default=0.9)
    args = parser.parse_args()

    with args.capture.open(newline="", encoding="utf-8") as capture_file:
        rows = list(csv.DictReader(capture_file))
    if not rows:
        raise SystemExit("Capture contains no samples")

    numeric_columns = (
        "elapsed_s",
        "setpoint_w",
        "ev_power_w",
        "ev_current_a",
        "ev_current_b",
        "ev_current_c",
        "csmb_current_a",
        "csmb_current_b",
        "csmb_current_c",
    )
    for row in rows:
        for column in numeric_columns:
            row[column] = float(row[column])

    response = [row for row in rows if row["phase"] == "response"]
    if not response:
        raise SystemExit("Capture contains no response phase")
    baseline = [row for row in rows if row["phase"] == "baseline"]

    transition = response[0]["elapsed_s"]
    final_start = response[-1]["elapsed_s"] - args.final_window
    final_rows = [row for row in response if row["elapsed_s"] > final_start]
    lower = args.target - args.tolerance
    upper = args.target + args.tolerance

    def in_band(row: dict[str, str | float]) -> bool:
        return lower <= row["ev_power_w"] <= upper

    settling_rows = [
        row
        for row in response
        if row["elapsed_s"] <= transition + args.settling_time
    ]
    off_count = sum(row["ev_power_w"] < 500 for row in response)
    high_count = sum(row["ev_power_w"] > 8000 for row in response)
    final_in_band = sum(in_band(row) for row in final_rows) / len(final_rows)
    first_stable_target = next(
        (
            window[0]["elapsed_s"] - transition
            for index in range(len(settling_rows) - args.settled_samples + 1)
            if all(
                in_band(row)
                for row in settling_rows[index : index + args.settled_samples]
            )
            for window in [settling_rows[index : index + args.settled_samples]]
        ),
        None,
    )
    reached_in_time = first_stable_target is not None
    errors = [row for row in rows if row["errors"]]
    baseline_setpoints = {row["setpoint_w"] for row in baseline}
    response_setpoints = {row["setpoint_w"] for row in response}

    print(f"Capture: {args.capture}")
    print(f"Samples: {len(rows)} ({len(response)} response), read errors: {len(errors)}")
    print(
        "Response power: "
        f"mean={statistics.mean(row['ev_power_w'] for row in response):.0f} W, "
        f"min={min(row['ev_power_w'] for row in response):.0f} W, "
        f"max={max(row['ev_power_w'] for row in response):.0f} W"
    )
    print(f"Off (<500 W): {off_count} s; high (>8000 W): {high_count} s")
    print(
        f"Final {args.final_window:.0f} s in {lower:.0f}-{upper:.0f} W: "
        f"{final_in_band:.1%}"
    )
    if first_stable_target is not None:
        print(f"First stable target window: {first_stable_target:.0f} s")

    checks = {
        "complete 3+3 minute capture": (
            len(rows) >= 360 and len(baseline) >= 180 and len(response) >= 180
        ),
        "single stable baseline setpoint": len(baseline_setpoints) == 1,
        "single expected response setpoint": response_setpoints == {args.target},
        "no read errors": not errors,
        "no charging pause": off_count == 0,
        f"target reached within {args.settling_time:.0f} s": reached_in_time,
        f"final window at least {args.required_in_band:.0%} in band": (
            final_in_band >= args.required_in_band
        ),
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())