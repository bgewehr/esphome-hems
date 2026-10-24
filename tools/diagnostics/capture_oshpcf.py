"""Passively capture OSSHPCF and heat-pump activity for a bounded period."""

from __future__ import annotations

import argparse
import datetime
from dataclasses import dataclass
import json
import math
import pathlib
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request


RELEVANT_LINE = re.compile(
    r"eebus_eg|SPINE registration|OSSHPCF|Semp|SmartEnergy|Storage writer|Fronius HP Power|EG1 MPC Leistung|"
    r"Connecting to|Successfully connected|Connection error|Disconnected",
    re.IGNORECASE,
)
HP_POWER = re.compile(r"Fronius HP Power' >> ([0-9.]+) W")
MPC_POWER = re.compile(r"EG1 MPC Leistung' >> ([0-9.]+) W")


@dataclass
class CaptureStats:
    semp_messages: int = 0
    replies: int = 0
    notifications: int = 0
    unclassified: int = 0
    read_replies: int = 0
    subscription_requests: int = 0
    subscription_confirmations: int = 0
    binding_requests: int = 0
    binding_confirmations: int = 0
    legacy_subscription_checks: int = 0

    def observe(self, line: str) -> None:
        if "OSSHPCF msg" in line and "SempData" in line:
            self.semp_messages += 1
            if re.search(r"\bcmd=reply\b", line):
                self.replies += 1
            elif re.search(r"\bcmd=notify\b", line):
                self.notifications += 1
            else:
                self.unclassified += 1
        self.read_replies += "OSSHPCF read reply:" in line
        self.subscription_requests += "OSSHPCF subscription request: result=sent" in line
        self.subscription_confirmations += "OSSHPCF subscription verified: present=true" in line
        self.binding_requests += "OSSHPCF binding request: result=sent" in line
        self.binding_confirmations += "OSSHPCF binding verified: present=true" in line
        self.legacy_subscription_checks += "remote SEMP entity" in line and "subscribe_err=0" in line


class StartReads:
    def __init__(self) -> None:
        self.previous: float | None = None
        self.last_sample: float | None = None
        self.low_since: float | None = None
        self.armed = True
        self.next_read: float | None = None
        self.remaining = 0
        self.series = 0

    def reset(self) -> None:
        self.previous = None
        self.last_sample = None
        self.low_since = None
        self.next_read = None
        self.remaining = 0
        self.armed = True

    def sample(self, power: float, now: float) -> bool:
        if not math.isfinite(power) or power < 0:
            self.reset()
            return False
        if self.last_sample is not None and now - self.last_sample > 15:
            self.reset()
        started = False
        if power <= 50:
            if self.low_since is None:
                self.low_since = now
            if now - self.low_since >= 60:
                self.armed = True
        else:
            if self.previous is not None and self.previous <= 50 and self.armed and not self.remaining:
                self.series += 1
                self.remaining = 10
                self.next_read = now
                self.armed = False
                started = True
            self.low_since = None
        self.previous = power
        self.last_sample = now
        return started

    def due(self, now: float) -> int | None:
        if self.last_sample is not None and now - self.last_sample > 15:
            self.reset()
        if not self.remaining or self.next_read is None or now < self.next_read:
            return None
        index = 11 - self.remaining
        self.remaining -= 1
        self.next_read = now + 60 if self.remaining else None
        return index


def request_semp_read(host: str) -> str:
    base = f"http://{host}"
    with urllib.request.urlopen(
        f"{base}/text_sensor/esphome_hems_eg1_osshpcf_read_status", timeout=3
    ) as response:
        status = json.load(response)["value"]
    if status not in (
        "Noch nicht angefordert", "Antwort mit Alternativen", "Antwort ohne Alternativen",
        "Timeout: keine Read-Antwort", "Read fehlgeschlagen (siehe Log)",
    ):
        return f"skipped: {status}"
    request = urllib.request.Request(
        f"{base}/button/esphome_hems_eg1_osshpcf_daten_lesen/press", data=b"", method="POST"
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        response.read()
    return "requested (reply must be confirmed in SEMP log)"


def enqueue_lines(stream, lines: queue.Queue) -> None:
    try:
        for line in stream:
            lines.put(line)
    finally:
        lines.put(None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="esphome-hems.local")
    parser.add_argument("--config", default="esphome-hems.yaml")
    parser.add_argument("--duration", type=int, default=24 * 60 * 60)
    parser.add_argument("--output-dir", default="private/captures")
    parser.add_argument("--start-reads", action="store_true")
    parser.add_argument("--esphome", default="esphome")
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
    stats = CaptureStats()
    reconnects = 0
    max_hp_power = 0.0
    max_mpc_power = 0.0
    start_reads = StartReads()
    api_disconnects = 0
    read_attempts = 0

    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        header = f"Capture started {started.isoformat()} for {args.duration} seconds\n"
        output.write(header)
        print(header, end="")

        def emit(message: str) -> None:
            line = f"CAPTURE {datetime.datetime.now().astimezone().isoformat()} {message}\n"
            output.write(line)
            output.flush()
            print(line, end="", flush=True)

        emit(f"start_reads={args.start_reads}; threshold=50 W; count=10; interval=60 s; rearm_low=60 s")

        while time.monotonic() < deadline:
            command = [args.esphome, "logs", args.config, "--device", args.host]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            lines = queue.Queue()
            reader = threading.Thread(target=enqueue_lines, args=(process.stdout, lines), daemon=True)
            reader.start()
            try:
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=min(1, max(0.001, deadline - time.monotonic())))
                    except queue.Empty:
                        line = ""
                    if line is None:
                        break
                    if RELEVANT_LINE.search(line):
                        output.write(line)
                        output.flush()
                        sys.stdout.write(line)
                        stats.observe(line)
                        max_hp_power = update_maximum(line, HP_POWER, max_hp_power)
                        max_mpc_power = update_maximum(line, MPC_POWER, max_mpc_power)
                    if "Disconnected" in line or "Connection error" in line:
                        api_disconnects += 1
                        start_reads.reset()
                        emit("connection_lost; read series cancelled")
                    if args.start_reads:
                        match = HP_POWER.search(line)
                        now = time.monotonic()
                        if match and start_reads.sample(float(match.group(1)), now):
                            emit(f"start series={start_reads.series}; power={match.group(1)} W")
                        index = start_reads.due(now) if now < deadline else None
                        if index is not None:
                            read_attempts += 1
                            try:
                                result = request_semp_read(args.host)
                            except Exception as error:
                                result = f"failed: {type(error).__name__}"
                            emit(f"read series={start_reads.series} index={index}/10 power={start_reads.previous} W; {result}")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                reader.join(timeout=2)
                process.stdout.close()
                start_reads.reset()

            if time.monotonic() < deadline:
                reconnects += 1
                time.sleep(min(5, max(0, deadline - time.monotonic())))

        finished = datetime.datetime.now().astimezone()
        summary = (
            f"Capture finished {finished.isoformat()}; SEMP messages={stats.semp_messages}; "
            f"SEMP replies={stats.replies}; SEMP notifications={stats.notifications}; "
            f"SEMP unclassified={stats.unclassified}; confirmed read replies={stats.read_replies}; "
            f"subscription requests={stats.subscription_requests}; "
            f"subscription confirmations={stats.subscription_confirmations}; "
            f"binding requests={stats.binding_requests}; binding confirmations={stats.binding_confirmations}; "
            f"legacy subscription checks={stats.legacy_subscription_checks}; logger reconnects={reconnects}; "
            f"API disconnects={api_disconnects}; event read attempts={read_attempts}; "
            f"max HP power={max_hp_power:.0f} W; max MPC power={max_mpc_power:.0f} W\n"
        )
        output.write(summary)
        print(summary, end="")

    print(f"Capture written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())