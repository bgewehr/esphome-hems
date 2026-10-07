"""Supervised live acceptance of battery priority and the EV solar gate."""

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
from test_fronius_storage_revert import read_storage


SWITCHES = (
    "charge_battery_first",
    "charge_ev_with_solar_support_only",
    "charge_ev_from_net_at_low_solar",
)
SENSORS = (
    "battery_priority_charge_target", "ev_solar_support", "fronius_battery_power",
    "fronius_ev_power", "ev_effektiver_sollwert", "ev_ladeabbrueche_5min",
)


def read(host: str, domain: str, name: str):
    url = f"http://{host}/{domain}/esphome_hems_{name}?_={time.time_ns()}"
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)["value"]


def set_switch(host: str, name: str, enabled: bool) -> None:
    if name not in SWITCHES:
        raise ValueError("Only solar policy switches may be changed")
    action = "turn_on" if enabled else "turn_off"
    request = urllib.request.Request(
        f"http://{host}/switch/esphome_hems_{name}/{action}", data=b"", method="POST"
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()


def stop_ev(host: str) -> None:
    request = urllib.request.Request(
        f"http://{host}/number/esphome_hems_ev_leistungsbegrenzung/set?value=0",
        data=b"", method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()
    if float(read(host, "number", "ev_leistungsbegrenzung")) != 0:
        raise RuntimeError("Emergency EV stop request not confirmed")


async def exercise(host: str, client: ModbusTcpClient, emit) -> None:
    original = {name: read(host, "switch", name) for name in SWITCHES}
    if original != dict(zip(SWITCHES, (True, False, False))):
        raise RuntimeError("Test requires battery priority on and both solar options off")
    user_limit = float(read(host, "number", "ev_leistungsbegrenzung"))
    aborts = float(read(host, "sensor", "ev_ladeabbrueche_5min"))
    if user_limit < 5544 or read(host, "text_sensor", "ev_regelstatus") != "Laedt":
        raise RuntimeError("Test requires an already charging EV above the minimum request")
    emit("baseline", switches=original, user_limit=user_limit, aborts=aborts)

    def sample(phase: str) -> dict:
        values = {name: float(read(host, "sensor", name)) for name in SENSORS}
        values["writer"] = read(host, "text_sensor", "storage_writer_status")
        values["policy"] = read(host, "text_sensor", "solar_policy_status")
        values["ev_status"] = read(host, "text_sensor", "ev_regelstatus")
        registers = read_storage(client)
        values["mode"] = registers[5]
        values["outwrte_raw"] = registers[12] if registers[12] < 32768 else registers[12] - 65536
        values["revert_s"] = registers[15]
        emit("sample", phase=phase, **values)
        if values["writer"] != "Automatic":
            raise RuntimeError(f"Storage writer unhealthy: {values['writer']}")
        if float(read(host, "number", "ev_leistungsbegrenzung")) != user_limit:
            raise RuntimeError("User EV request changed; stopping test")
        if values["ev_ladeabbrueche_5min"] > aborts:
            raise RuntimeError("EV abort count increased")
        return values

    async def observe(phase: str, duration: float, predicate) -> None:
        started = time.monotonic()
        accepted = 0
        while time.monotonic() - started < duration:
            values = sample(phase)
            accepted = accepted + 1 if predicate(values) else 0
            if accepted >= 3:
                emit("phase_passed", phase=phase, elapsed_s=round(time.monotonic() - started, 2))
                return
            await asyncio.sleep(2)
        raise RuntimeError(f"Acceptance not observed: {phase}")

    try:
        await observe("battery_priority", 45, lambda values:
            values["mode"] == 2 and values["outwrte_raw"] < 0 and values["revert_s"] == 15
            and values["fronius_battery_power"] < -100)
        set_switch(host, SWITCHES[0], False)
        await observe("priority_disabled", 35, lambda values: values["outwrte_raw"] >= 0)
        set_switch(host, SWITCHES[0], True)
        await observe("priority_restored", 45, lambda values:
            values["mode"] == 2 and values["outwrte_raw"] < 0 and values["ev_solar_support"] < 300)
        set_switch(host, SWITCHES[1], True)
        await observe("solar_pause", 65, lambda values:
            values["ev_effektiver_sollwert"] == 0 and values["fronius_ev_power"] < 300)
        set_switch(host, SWITCHES[1], False)
        await observe("ev_resumed", 90, lambda values:
            values["ev_effektiver_sollwert"] >= 5544 and values["fronius_ev_power"] > 1000)
    except Exception:
        stop_ev(host)
        emit("emergency_stop", previous_user_limit=user_limit)
        raise
    finally:
        errors = []
        for name in reversed(SWITCHES):
            try:
                set_switch(host, name, original[name])
            except Exception as error:
                errors.append(f"{name}: {error}")
        await asyncio.sleep(2)
        for name in SWITCHES:
            try:
                if read(host, "switch", name) != original[name]:
                    errors.append(f"{name}: restore readback mismatch")
            except Exception as error:
                errors.append(f"{name}: {error}")
        emit("restoration", errors=errors)
        if errors:
            raise RuntimeError("SWITCH RESTORATION FAILED: " + "; ".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.178.24")
    parser.add_argument("--expected-build", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or read(args.host, "text_sensor", "build_time") != args.expected_build:
        parser.error("Requires --execute and matching live build")
    directory = Path(__file__).resolve().parents[2] / "private" / "captures"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"solar-policy-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with path.open("x", encoding="utf-8") as stream:
        def emit(event: str, **values) -> None:
            line = json.dumps({"time": datetime.now().astimezone().isoformat(), "event": event, **values})
            print(line, flush=True)
            stream.write(line + "\n")
            stream.flush()

        host, port = get_fronius_endpoint()
        with ModbusTcpClient(host, port=port, timeout=2, retries=0) as client:
            try:
                asyncio.run(exercise(args.host, client, emit))
            except Exception as error:
                emit("failed", error=str(error))
                raise
        emit("passed", scope="battery priority on/off, EV solar pause and manual policy release")
    print(f"Evidence: {path}")


if __name__ == "__main__":
    main()