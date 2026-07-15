"""Capture ESPHome API logs across a controlled web-server restart."""

from __future__ import annotations

import argparse
import datetime
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.178.24")
    parser.add_argument("--config", default="esphome-hems.yaml")
    parser.add_argument("--duration", type=int, default=90)
    parser.add_argument("--output-dir", default="private/captures")
    return parser.parse_args()


def copy_output(process: subprocess.Popen[str], output) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        output.write(line)
        output.flush()


def main() -> int:
    args = parse_args()
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    repository_root = pathlib.Path(__file__).resolve().parents[2]
    output_dir = pathlib.Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = repository_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"esphome-restart-{timestamp}.log"

    command = ["esphome", "logs", args.config, "--device", args.host]
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        reader = threading.Thread(target=copy_output, args=(process, output), daemon=True)
        reader.start()

        try:
            time.sleep(5)
            restart_url = f"http://{args.host}/button/esphome_hems_restart/press"
            request = urllib.request.Request(restart_url, method="POST", data=b"")
            with urllib.request.urlopen(request, timeout=10) as response:
                print(f"Restart requested: HTTP {response.status}")
            time.sleep(args.duration)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=5)

    print(f"Capture written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())