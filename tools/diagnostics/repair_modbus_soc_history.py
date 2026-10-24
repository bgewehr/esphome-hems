"""Guarded offline SOC repair: delete invalid registers and apply decimal factors."""

import argparse
from bisect import bisect_left
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import socket
import sqlite3

from audit_modbus_history import number


ENTITY = "sensor.hausanschlussraum_bg_hems_esphome_hems_fronius_battery_soc_2"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def weighted(rows, start, end):
    rows = [row for row in rows if number(row[2]) is not None]
    timestamps = [row[1] for row in rows]
    position = max(0, bisect_left(timestamps, start) - 1)
    integral = duration = 0.0
    values = []
    for index in range(position, len(rows)):
        row = rows[index]
        if row[1] >= end:
            break
        following = rows[index + 1][1] if index + 1 < len(rows) else end
        seconds = min(end, following) - max(start, row[1])
        value = number(row[2])
        if value is not None and seconds > 0:
            integral += value * seconds
            duration += seconds
            values.append(value)
    return {
        "mean": integral / duration if duration else None,
        "mean_weight": duration,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def verify_statistics(actual, expected):
    for field in ("mean", "min", "max"):
        left, right = actual[field], expected[field]
        if left is None or right is None:
            if left != right:
                raise ValueError(f"Cannot reproduce {field}: {left} != {right}")
        elif not math.isclose(left, right, rel_tol=1e-8, abs_tol=1e-5):
            raise ValueError(f"Cannot reproduce {field}: {left} != {right}")


def aggregate(rows):
    populated = [row for row in rows if row["mean"] is not None]
    if not populated:
        return {"mean": None, "min": None, "max": None}
    weights = [row["mean_weight"] if row["mean_weight"] is not None else 1.0 for row in populated]
    return {
        "mean": sum(row["mean"] * weight for row, weight in zip(populated, weights)) / sum(weights),
        "min": min(row["min"] for row in populated if row["min"] is not None),
        "max": max(row["max"] for row in populated if row["max"] is not None),
    }


def make_plan(connection, invalid):
    connection.row_factory = sqlite3.Row
    metadata_id = connection.execute("SELECT metadata_id FROM states_meta WHERE entity_id=?", (ENTITY,)).fetchone()[0]
    statistics_id = connection.execute("SELECT id FROM statistics_meta WHERE statistic_id=?", (ENTITY,)).fetchone()[0]
    rows = connection.execute(
        "SELECT state_id,last_updated_ts,state,old_state_id FROM states WHERE metadata_id=? ORDER BY last_updated_ts,state_id",
        (metadata_id,),
    ).fetchall()
    deletion_ids = {item["state_id"] for item in invalid if item["entity"] == ENTITY}
    if len(deletion_ids) != len(invalid):
        raise ValueError("Unexpected non-SOC deletion requires its own register verification")
    changes = []
    for index, row in enumerate(rows):
        value = number(row[2])
        if row[0] in deletion_ids:
            evidence = next(item for item in invalid if item["state_id"] == row[0])
            if row[2] != evidence["recorded_state"] or evidence["invalid_raw"] != 65535:
                raise ValueError("Invalid-register evidence mismatch")
            replacement = None
        elif value is not None and value > 100:
            replacement = str(Decimal(row[2]) * Decimal("0.1"))
            corrected = float(replacement)
            before = next((number(previous[2]) for previous in reversed(rows[:index]) if number(previous[2]) is not None), None)
            after = next((number(following[2]) for following in rows[index + 1:] if number(following[2]) is not None), None)
            if not 0 <= corrected <= 100 or before is None or after is None:
                raise ValueError("SOC factor not uniquely supported")
            if not all(math.isclose(corrected, neighbor, rel_tol=0.03) for neighbor in (before, after)):
                raise ValueError("SOC factor contradicts neighboring measurements")
        else:
            continue
        changes.append({
            "state_id": row[0], "metadata_id": metadata_id, "timestamp": row[1],
            "old_state": row[2], "new_state": replacement, "old_state_id": row[3],
            "multiplier": "0.1" if replacement is not None else None,
            "next_timestamp": rows[index + 1][1] if index + 1 < len(rows) else None,
        })
    if {item["state_id"] for item in changes if item["new_state"] is None} != deletion_ids:
        raise ValueError("Deletion target missing")
    replacements = {item["state_id"]: item["new_state"] for item in changes}
    updated = [
        (row[0], row[1], replacements.get(row[0], row[2]), row[3])
        for row in rows if row[0] not in deletion_ids
    ]
    starts = set()
    for item in changes:
        if item["next_timestamp"] is None:
            raise ValueError("Cannot repair open-ended last state")
        starts.update(range(int(item["timestamp"] // 300) * 300, int(item["next_timestamp"] // 300) * 300 + 1, 300))
    short_changes = []
    for start in sorted(starts):
        original = connection.execute(
            "SELECT * FROM statistics_short_term WHERE metadata_id=? AND start_ts=?", (statistics_id, start)
        ).fetchone()
        if original is None:
            raise ValueError(f"Missing short-term statistics at {start}")
        calculated = weighted(rows, start, start + 300)
        verify_statistics(original, calculated)
        corrected = weighted(updated, start, start + 300)
        if original["mean_weight"] is None:
            corrected["mean_weight"] = None
        elif not math.isclose(original["mean_weight"], calculated["mean_weight"], abs_tol=1e-5):
            raise ValueError("Unknown statistics weighting")
        short_changes.append({"table": "statistics_short_term", "id": original["id"], "start_ts": start, "before": dict(original), "after": corrected})
    hourly_changes = []
    short_by_id = {item["id"]: item["after"] for item in short_changes}
    for start in sorted({timestamp // 3600 * 3600 for timestamp in starts}):
        original = connection.execute(
            "SELECT * FROM statistics WHERE metadata_id=? AND start_ts=?", (statistics_id, start)
        ).fetchone()
        short = [dict(row) for row in connection.execute(
            "SELECT * FROM statistics_short_term WHERE metadata_id=? AND start_ts>=? AND start_ts<? ORDER BY start_ts",
            (statistics_id, start, start + 3600),
        )]
        if original is None or len(short) != 12:
            raise ValueError("Incomplete hour; cannot safely recompute statistics")
        verify_statistics(original, aggregate(short))
        corrected = aggregate([dict(row, **short_by_id.get(row["id"], {})) for row in short])
        hourly_changes.append({"table": "statistics", "id": original["id"], "start_ts": start, "before": dict(original), "after": corrected})
    return {"entity": ENTITY, "states": changes, "statistics": short_changes + hourly_changes}


def apply_plan(connection, plan):
    for item in plan["states"]:
        original = connection.execute(
            "SELECT state,old_state_id FROM states WHERE state_id=? AND metadata_id=? AND last_updated_ts=?",
            (item["state_id"], item["metadata_id"], item["timestamp"]),
        ).fetchone()
        if original is None or tuple(original) != (item["old_state"], item["old_state_id"]):
            raise ValueError("State changed since planning")
        if item["new_state"] is None:
            connection.execute("UPDATE states SET old_state_id=? WHERE old_state_id=? AND metadata_id=?",
                               (item["old_state_id"], item["state_id"], item["metadata_id"]))
            connection.execute("DELETE FROM states WHERE state_id=?", (item["state_id"],))
        else:
            connection.execute("UPDATE states SET state=? WHERE state_id=?", (item["new_state"], item["state_id"]))
    for item in plan["statistics"]:
        original = connection.execute("SELECT * FROM " + item["table"] + " WHERE id=?", (item["id"],)).fetchone()
        if dict(original) != item["before"]:
            raise ValueError("Statistics changed since planning")
        fields = list(item["after"])
        connection.execute(
            "UPDATE " + item["table"] + " SET " + ",".join(field + "=?" for field in fields) + " WHERE id=?",
            [item["after"][field] for field in fields] + [item["id"]],
        )


def self_test():
    rows = [(1, -100, "92.4000015258789"), (2, 145.3991404, "655.349975585938"), (3, 166.6606836, "92.3000030517578")]
    original = weighted(rows, 0, 300)
    assert math.isclose(original["mean"], 132.2528412430004, abs_tol=1e-5)
    corrected = weighted([rows[0], rows[2]], 0, 300)
    assert corrected["max"] < 100 and corrected["mean"] < 100
    assert weighted([(1, 0, "unknown"), (2, 100, "19.7")], 0, 300)["mean_weight"] == 200
    interrupted = weighted([(1, -1, "19.7"), (2, 100, "unknown"), (3, 200, "19.7")], 0, 300)
    assert interrupted["mean_weight"] == 300 and math.isclose(interrupted["mean"], 19.7)
    assert Decimal("197.0") * Decimal("0.1") == Decimal("19.70")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE states(state_id INTEGER,metadata_id INTEGER,last_updated_ts REAL,state TEXT,old_state_id INTEGER)")
    connection.executemany("INSERT INTO states VALUES(?,?,?,?,?)", [(1, 1, 0, "19.7", None), (2, 1, 1, "655.35", 1), (3, 1, 2, "197", 2)])
    plan = {"states": [{"state_id": 2, "metadata_id": 1, "timestamp": 1, "old_state": "655.35", "new_state": None, "old_state_id": 1}], "statistics": []}
    apply_plan(connection, plan)
    assert connection.execute("SELECT COUNT(*) FROM states WHERE state_id=2").fetchone()[0] == 0
    assert connection.execute("SELECT old_state_id FROM states WHERE state_id=3").fetchone()[0] == 1
    connection.close()
    print("PASS: recorded mean reproduced, invalid value removed, state chain repaired, decimal correction exact")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--invalid", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not all((args.database, args.backup, args.invalid, args.output)):
        parser.error("--database, --backup, --invalid and --output are required")
    if args.output.exists():
        raise ValueError("Refusing to overwrite existing repair receipt")
    backup = sqlite3.connect(args.backup.resolve().as_uri() + "?mode=ro", uri=True)
    if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("Backup integrity check failed")
    plan = make_plan(backup, json.loads(args.invalid.read_text(encoding="utf-8")))
    backup.close()
    print("Verified plan:", len(plan["states"]), "states,", len(plan["statistics"]), "statistics", flush=True)
    plan["status"] = "validated_not_applied"
    if args.apply:
        if args.database.resolve() == args.backup.resolve():
            raise ValueError("Cannot modify backup")
        try:
            service = socket.create_connection(("mediasafe2", 8123), timeout=2)
        except OSError:
            pass
        else:
            service.close()
            raise ValueError("Home Assistant is reachable; keep it stopped during repair")
        wal = Path(str(args.database) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("Nonempty source WAL")
        print("Verifying source against full backup...", flush=True)
        plan["backup_sha256"] = digest(args.backup)
        if digest(args.database) != plan["backup_sha256"]:
            raise ValueError("Source differs from backup; refusing repair")
        args.output.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        connection = sqlite3.connect(args.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("BEGIN EXCLUSIVE")
            apply_plan(connection, plan)
            connection.commit()
            if tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())[0] != 0:
                raise ValueError("Checkpoint incomplete; keep Home Assistant stopped")
            for item in plan["states"]:
                state = connection.execute("SELECT state FROM states WHERE state_id=?", (item["state_id"],)).fetchone()
                if (state[0] if state else None) != item["new_state"]:
                    raise ValueError("Readback mismatch")
            plan["status"] = "applied_and_verified"
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    args.output.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(plan["status"], flush=True)


if __name__ == "__main__":
    main()