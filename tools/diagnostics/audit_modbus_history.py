"""Read-only decimal-scale audit of a Home Assistant recorder snapshot."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import struct


def invalid_register_values(unsigned):
    raw = 65535 if unsigned else -32768
    return {
        struct.unpack("f", struct.pack("f", raw * 10.0**scale))[0]: scale
        for scale in range(-10, 11)
    }


def sentinel_scan(database, report):
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    findings = []
    try:
        for item in report["entities"]:
            if item["source"] != "hems_modbus":
                continue
            unsigned = "battery_soc" in item["entity"]
            values = invalid_register_values(unsigned)
            for state_id, timestamp, state in connection.execute(
                "SELECT state_id,last_updated_ts,state FROM states WHERE metadata_id=? ORDER BY last_updated_ts",
                (item["metadata_id"],),
            ):
                value = number(state)
                if value is None or abs(value) > 3.4e38:
                    continue
                encoded = struct.unpack("f", struct.pack("f", value))[0]
                matches = [candidate for candidate in (encoded, -encoded) if candidate in values]
                if matches:
                    findings.append({
                        "entity": item["entity"], "metadata_id": item["metadata_id"],
                        "state_id": state_id, "timestamp": timestamp, "utc": iso(timestamp),
                        "recorded_state": state, "invalid_raw": 65535 if unsigned else -32768,
                        "scale": values[matches[0]], "action": "delete",
                    })
    finally:
        connection.close()
    return findings


FACTORS = (-3, -2, -1, 1, 2, 3)
TOLERANCE = 0.03


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def close(left, right):
    return abs(left - right) <= TOLERANCE * max(abs(left), abs(right), 1e-9)


def decimal_shift(previous, current):
    if not previous or not current or previous * current < 0:
        return None
    ratio = current / previous
    return next((power for power in FACTORS if close(ratio, 10.0**power)), None)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def point(row):
    return {"state_id": row[0], "utc": iso(row[1]), "value": row[2]}


def analyze(rows):
    valid = [(row[0], row[1], number(row[2])) for row in rows]
    valid = [row for row in valid if row[2] is not None and row[1] is not None]
    changes = []
    for index in range(1, len(valid)):
        before, current = valid[index - 1], valid[index]
        power = decimal_shift(before[2], current[2])
        if power is None:
            continue
        finding = {
            "before": point(before),
            "suspect": point(current),
            "factor": 10.0**power,
            "gap_seconds": current[1] - before[1],
            "kind": "factor_transition_only",
        }
        if current[1] - before[1] <= 300:
            for following in valid[index + 1 : index + 101]:
                if following[1] - current[1] > 120:
                    break
                if close(following[2], before[2]):
                    finding.update(
                        kind="short_excursion_with_return",
                        returned=point(following),
                        duration_seconds=following[1] - current[1],
                    )
                    break
                if not close(following[2] / (10.0**power), before[2]):
                    break
        changes.append(finding)
    return valid, changes


def source_for(entity, registered):
    platform = registered.get(entity, {}).get("platform", "")
    if "modbus" in platform:
        return platform
    if "hems" in entity and "fronius" in entity:
        if any(name in entity for name in (
            "ac_power", "grid_power", "grid_current", "ev_power", "ev_current",
            "hp_power", "battery_soc", "mppt",
        )):
            return "hems_modbus"
        return "hems_derived_or_solar_api"
    if "hems" in entity and any(name in entity for name in (
        "xemex", "battery_max_discharge", "storage_", "strom_l",
    )):
        return "hems_derived_or_control"
    if entity.startswith(("sensor.fronius_symo_gen24_", "sensor.fronius_smart_meter_")):
        return "historical_fronius_source_unverified"
    return None


def audit(database, registry):
    registered = {
        item["entity_id"]: item
        for item in json.loads(registry.read_text(encoding="utf-8"))["data"]["entities"]
    }
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "method": {
            "factors": [10, 100, 1000], "directions": ["multiply", "divide"],
            "relative_tolerance": TOLERANCE,
            "short_excursion_max_seconds": 120,
            "neighbor_max_gap_seconds": 300,
            "scope": "All retained states and statistics for selected sources",
            "warning": "Candidates only; legitimate load changes can match. No database writes.",
        },
        "entities": [],
    }
    try:
        for metadata_id, entity in connection.execute("SELECT metadata_id, entity_id FROM states_meta"):
            source = source_for(entity, registered)
            if source is None or not entity.startswith("sensor."):
                continue
            rows = connection.execute(
                "SELECT state_id,last_updated_ts,state FROM states "
                "WHERE metadata_id=? ORDER BY last_updated_ts,state_id", (metadata_id,)
            ).fetchall()
            valid, changes = analyze(rows)
            summary = {
                "entity": entity, "source": source, "metadata_id": metadata_id,
                "rows": len(rows), "numeric_rows": len(valid),
                "first_utc": iso(rows[0][1]) if rows else None,
                "last_utc": iso(rows[-1][1]) if rows else None,
                "minimum": min((row[2] for row in valid), default=None),
                "maximum": max((row[2] for row in valid), default=None),
                "candidates": changes,
            }
            if "soc" in entity:
                summary["outside_soc_bounds"] = [point(row) for row in valid if not 0 <= row[2] <= 100]
            statistics_meta = connection.execute(
                "SELECT id,unit_of_measurement,has_mean,has_sum FROM statistics_meta WHERE statistic_id=?",
                (entity,),
            ).fetchone()
            summary["statistics"] = {}
            if statistics_meta:
                summary["statistics_metadata"] = dict(zip(
                    ("id", "unit", "has_mean", "has_sum"), statistics_meta
                ))
                for table in ("statistics_short_term", "statistics"):
                    statistics = connection.execute(
                        "SELECT id,start_ts,mean,min,max,state FROM " + table +
                        " WHERE metadata_id=? ORDER BY start_ts", (statistics_meta[0],)
                    ).fetchall()
                    fields = {}
                    for offset, field in ((2, "mean"), (3, "min"), (4, "max"), (5, "state")):
                        _, shifts = analyze([(row[0], row[1], row[offset]) for row in statistics])
                        fields[field] = shifts
                    summary["statistics"][table] = {
                        "rows": len(statistics),
                        "first_utc": iso(statistics[0][1]) if statistics else None,
                        "last_utc": iso(statistics[-1][1]) if statistics else None,
                        "candidates_by_field": fields,
                        "outside_soc_bounds": [
                            dict(zip(("id", "start_ts", "mean", "min", "max", "state"), row))
                            for row in statistics if "soc" in entity
                            and any(value is not None and not 0 <= value <= 100 for value in row[2:])
                        ],
                    }
            report["entities"].append(summary)
            print(entity, "rows", len(rows), "candidates", len(changes), flush=True)
    finally:
        connection.close()
    report["totals"] = {
        "entities": len(report["entities"]),
        "states": sum(item["rows"] for item in report["entities"]),
        "numeric_states": sum(item["numeric_rows"] for item in report["entities"]),
        "sources": dict(Counter(item["source"] for item in report["entities"])),
        "candidate_types": dict(Counter(
            change["kind"] for item in report["entities"] for change in item["candidates"]
        )),
    }
    return report


def self_test():
    for power in FACTORS:
        for sign in (-1, 1):
            baseline = sign * 19.7
            _, changes = analyze([(1, 0, baseline), (2, 10, baseline * 10**power), (3, 12, baseline)])
            assert changes[0]["factor"] == 10**power
            assert changes[0]["kind"] == "short_excursion_with_return"
    assert decimal_shift(0, 100) is None
    assert decimal_shift(10, -100) is None
    assert decimal_shift(100, 130) is None
    assert 655.3499755859375 in invalid_register_values(True)
    assert -327.67999267578125 in invalid_register_values(False)
    assert 197 not in invalid_register_values(True)
    assert analyze([(1, 0, "unknown"), (2, 1, "nan"), (3, 2, "inf")]) == ([], [])
    _, changes = analyze([(1, 0, 19.7), (2, 1, "unavailable"), (3, 2, 197), (4, 4, 19.7)])
    assert changes[0]["kind"] == "short_excursion_with_return"
    _, changes = analyze([(1, 0, 19.7), (2, 400, 197), (3, 402, 19.7)])
    assert changes[0]["kind"] == "factor_transition_only"
    _, changes = analyze([(1, 0, 19.7), (2, 10, 197), (3, 140, 19.7)])
    assert changes[0]["kind"] == "factor_transition_only"
    _, changes = analyze([(1, 0, 10), (2, 10, 100), (3, 11, 50), (4, 12, 10)])
    assert changes[0]["kind"] == "factor_transition_only"
    print("PASS: factors in both directions, signed values, zero, missing values, gaps and returns")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sentinel-scan", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.sentinel_scan:
        if not args.database or not args.output:
            parser.error("--database and --output are required")
        findings = sentinel_scan(args.database, json.loads(args.sentinel_scan.read_text(encoding="utf-8")))
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(findings, output, indent=2, allow_nan=False)
        print(json.dumps(findings, indent=2))
        return
    if not all((args.database, args.registry, args.output)):
        parser.error("--database, --registry and --output are required")
    report = audit(args.database, args.registry)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, allow_nan=False)
    print(json.dumps(report["totals"], indent=2))


if __name__ == "__main__":
    main()