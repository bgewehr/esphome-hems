"""Supervised IC124 write-inactivity test; never requests forced charging."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import time
import urllib.request

from pymodbus.client import ModbusTcpClient

from env_config import get_fronius_endpoint


def read_storage(client: ModbusTcpClient) -> list[int]:
    response = client.read_holding_registers(40343, count=26, device_id=1)
    if response.isError() or len(response.registers) != 26:
        raise RuntimeError(f"Storage read failed: {response}")
    return response.registers


def validate_baseline(registers: list[int]) -> None:
    if len(registers) != 26 or registers[:2] != [124, 24]:
        raise RuntimeError("Unexpected storage model")
    if registers[5] not in (0, 2) or registers[12] != 0 or registers[13] != 10000:
        raise RuntimeError("Test requires mode 0 or 2, OutWRte 0%, InWRte 100%")
    if registers[15] != 0 or registers[25] != 65534 or registers[22] != 65534:
        raise RuntimeError("Test requires zero revert time and verified -2 scaling")
    if registers[11] not in (4, 6) or not 2000 <= registers[8] <= 9000:
        raise RuntimeError("Test requires charging or holding battery with SOC 20..90%")


def write_register(client: ModbusTcpClient, address: int, value: int) -> None:
    if address not in (40348, 40355, 40358) or not 0 <= value <= 32767:
        raise ValueError("Only nonnegative test/restore writes are allowed")
    response = client.write_registers(address, [value], device_id=1)
    if response.isError():
        raise RuntimeError(f"Register {address} rejected: {response}")


def hems_value(host: str, name: str) -> str:
    request = urllib.request.Request(
        f"http://{host}/text_sensor/esphome_hems_{name}?_={time.time_ns()}",
        headers={"Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.load(response)
    return str(payload["value"])


def press(host: str, name: str) -> None:
    request = urllib.request.Request(
        f"http://{host}/button/esphome_hems_storage_test_{name}/press",
        data=b"", method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        response.read()


async def run_test(client: ModbusTcpClient, hems_host: str, emit) -> str:
    if hems_value(hems_host, "storage_writer_status") != "Automatic":
        raise RuntimeError("HEMS writer is not in automatic mode")
    baseline = read_storage(client)
    emit("baseline", registers=baseline)
    try:
        validate_baseline(baseline)
    except RuntimeError as error:
        emit("precondition_failed", error=str(error))
        return "precondition_not_met"
    outcome = "not_started"
    pause_requested = False
    writes_started = False
    try:
        pause_requested = True
        press(hems_host, "pause_180s")
        for _ in range(6):
            await asyncio.sleep(2)
            if hems_value(hems_host, "storage_writer_status") != "Test pause (max 180s)":
                raise RuntimeError("HEMS pause not confirmed")
        baseline = read_storage(client)
        validate_baseline(baseline)
        emit("paused_baseline", registers=baseline)
        writes_started = True
        try:
            write_register(client, 40358, 15)
        except RuntimeError as error:
            emit("timeout_write_rejected", error=str(error))
            return "timeout_write_rejected"
        current = read_storage(client)
        emit("timeout_readback", registers=current)
        if current[15] != 15:
            return "timeout_not_retained"
        write_register(client, 40355, 0)
        write_register(client, 40348, 2)
        started = time.monotonic()
        activated = False
        outcome = "no_revert_observed"
        while time.monotonic() - started <= 60:
            elapsed = time.monotonic() - started
            if hems_value(hems_host, "storage_writer_status") != "Test pause (max 180s)":
                raise RuntimeError("HEMS resumed during observation; result invalid")
            current = read_storage(client)
            emit("sample", elapsed_s=round(elapsed, 2), registers=current)
            if current[5] == 2 and current[12] == 0:
                activated = True
            elif activated and (current[5] == 0 or (current[5] == 2 and current[12] == 10000)):
                outcome = "revert_observed" if elapsed >= 10 else "early_change_inconclusive"
                break
            elif not activated and elapsed >= 5:
                outcome = "activation_not_observed"
                break
            await asyncio.sleep(2)
        return outcome
    finally:
        cleanup_errors = []
        if writes_started:
            for address, value in ((40348, 0), (40355, baseline[12]),
                                   (40358, baseline[15]), (40348, baseline[5])):
                try:
                    write_register(client, address, value)
                except Exception as error:
                    cleanup_errors.append(f"{address}: {error}")
            try:
                restored = read_storage(client)
                emit("restored", registers=restored)
                if [restored[index] for index in (5, 12, 15)] != [baseline[index] for index in (5, 12, 15)]:
                    cleanup_errors.append("Register restoration readback mismatch")
            except Exception as error:
                cleanup_errors.append(str(error))
        if pause_requested:
            try:
                press(hems_host, "resume")
                for _ in range(8):
                    await asyncio.sleep(2)
                    if hems_value(hems_host, "storage_writer_status") == "Automatic":
                        emit("automatic_resumed")
                        break
                else:
                    cleanup_errors.append("Automatic writer did not resume")
            except Exception as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            raise RuntimeError("RESTORATION FAILED: " + "; ".join(cleanup_errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--hems-host", default="192.168.178.24")
    parser.add_argument("--expected-build")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Supervised hardware test requires --execute and --expected-build")
    if not args.expected_build or hems_value(args.hems_host, "build_time") != args.expected_build:
        parser.error("Live firmware build does not match --expected-build")
    output = Path(__file__).resolve().parents[2] / "private" / "captures"
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"fronius-revert-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with path.open("x", encoding="utf-8") as stream:
        def emit(event: str, **data) -> None:
            line = json.dumps({"time": datetime.now().astimezone().isoformat(), "event": event, **data})
            print(line, flush=True)
            stream.write(line + "\n")
            stream.flush()

        host, port = get_fronius_endpoint()
        with ModbusTcpClient(host, port=port, timeout=2, retries=0) as client:
            outcome = asyncio.run(run_test(client, args.hems_host, emit))
        emit("result", outcome=outcome, scope="write inactivity with Modbus reads continuing")
    print(f"Evidence: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())