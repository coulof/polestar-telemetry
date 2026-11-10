"""Poll telemetry and store snapshots in SQLite (deduped on metaEventId)."""
import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import httpx

from . import config
from .client import DOMAINS, PolestarClient
from .derive import CHARGING_STATES

DB_PATH = os.environ.get("DB_PATH", "data/telemetry.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    vin TEXT NOT NULL,
    domain TEXT NOT NULL,
    event_id TEXT NOT NULL,
    ts REAL NOT NULL,          -- vehicle event time, epoch seconds
    received_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (vin, domain, event_id)
);
CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots (vin, domain, ts);

CREATE TABLE IF NOT EXISTS reports (
    week TEXT PRIMARY KEY,      -- ISO week, e.g. 2026-W46
    created_at TEXT NOT NULL,
    model TEXT,
    markdown TEXT NOT NULL,
    stats TEXT NOT NULL         -- the aggregated JSON the report was written from
);

CREATE VIEW IF NOT EXISTS readings AS
SELECT b.vin, b.ts,
       json_extract(b.payload, '$.batteryChargeLevelPercentage') AS soc_pct,
       json_extract(b.payload, '$.estimatedDistanceToEmptyKm') AS range_km,
       json_extract(b.payload, '$.chargingStatusV2') AS charging_status,
       json_extract(b.payload, '$.chargingType') AS charging_type,
       json_extract(b.payload, '$.chargingPowerWatts') AS charging_power_w,
       json_extract(b.payload, '$.chargingCurrentAmps') AS charging_current_a,
       json_extract(b.payload, '$.averageEnergyConsumptionKwhPer100Km') AS avg_kwh_100km
FROM snapshots b WHERE b.domain = 'battery';
"""


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")  # collector writes while the dashboard reads
    db.executescript(SCHEMA)
    return db


def event_ts(data):
    t = data.get("timestamp") or {}
    if "seconds" in t:
        return int(t["seconds"]) + t.get("nanos", 0) / 1e9
    return time.time()


def store(db, vin, domain, data):
    """Insert a snapshot; returns True if it was new."""
    event_id = data.get("metaEventId") or f"{event_ts(data)}"
    cur = db.execute(
        "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?)",
        (vin, domain, event_id, event_ts(data), data.get("metaReceivedAt"), json.dumps(data)),
    )
    return cur.rowcount == 1


def collect_once(client, db):
    new = {}
    for vin in client.vehicles()["data"]:
        vin = vin if isinstance(vin, str) else vin["vin"]
        for domain in DOMAINS:
            try:
                data = client.telemetry(vin, domain)["data"]
            except httpx.HTTPStatusError as e:
                print(f"{domain}: HTTP {e.response.status_code}")
                continue
            new[domain] = new.get(domain, 0) + store(db, vin, domain, data)
    db.commit()
    return new


def is_active(db):
    """True while the car is charging or being driven (latest events)."""
    def latest(domain, key):
        row = db.execute(
            "SELECT json_extract(payload, ?) FROM snapshots WHERE domain = ? ORDER BY ts DESC LIMIT 1",
            (f"$.{key}", domain),
        ).fetchone()
        return row[0] if row else None

    return latest("battery", "chargingStatusV2") in CHARGING_STATES or latest(
        "availability", "usageMode"
    ) in ("USAGE_MODE_DRIVING", "USAGE_MODE_ACTIVE", "USAGE_MODE_ENGINE_ON")


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, help="seconds between polls when idle (overrides config.toml)")
    ap.add_argument("--interval-active", type=int, help="seconds while charging/driving (overrides config.toml)")
    args = ap.parse_args()

    client, db = PolestarClient(), connect()
    while True:
        cfg = config.load()  # re-read each cycle so edits apply without a restart
        idle = args.interval or cfg["interval"]
        active = args.interval_active or cfg["interval_active"]
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        try:
            print(stamp, "new rows:", collect_once(client, db), flush=True)
        except httpx.HTTPError as e:
            print(stamp, "error:", type(e).__name__, flush=True)
        if args.once:
            break
        time.sleep(active if is_active(db) else idle)


if __name__ == "__main__":
    main()
