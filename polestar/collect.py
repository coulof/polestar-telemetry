"""Poll telemetry and store snapshots in SQLite (deduped on metaEventId)."""
import argparse
import hashlib
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

CREATE TABLE IF NOT EXISTS report_failures (
    week TEXT PRIMARY KEY,          -- ISO week whose report could not be written
    failed_at TEXT NOT NULL,
    error TEXT NOT NULL             -- which step failed, never data
);

CREATE TABLE IF NOT EXISTS session_costs (
    start_ts INTEGER PRIMARY KEY,   -- charge session start, epoch seconds UTC
    cost REAL NOT NULL,             -- total paid in EUR (energy and fees), replaces the estimate
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_ok REAL NOT NULL           -- epoch seconds of the last poll that fetched data from the API
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
    # without an event id, dedupe on the content: a time-based id would insert the same payload on every poll
    event_id = data.get("metaEventId") or hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
    cur = db.execute(
        "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?)",
        (vin, domain, event_id, event_ts(data), data.get("metaReceivedAt"), json.dumps(data)),
    )
    return cur.rowcount == 1


def collect_once(client, db):
    """Poll every domain once. A failing domain (HTTP error, timeout) is skipped so the others still
    get stored; whatever was fetched is committed even if a later call raises."""
    new = {}
    fetched = False
    try:
        for vin in client.vehicles()["data"]:
            vin = vin if isinstance(vin, str) else vin["vin"]
            for domain in DOMAINS:
                try:
                    data = client.telemetry(vin, domain)["data"]
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 404:   # 404 = the car has no data for this domain (e.g. charge locations)
                        print(f"{domain}: HTTP {e.response.status_code}")
                    continue
                except httpx.HTTPError as e:            # timeout, connection reset: skip this domain only
                    print(f"{domain}: {type(e).__name__}")
                    continue
                fetched = True
                new[domain] = new.get(domain, 0) + store(db, vin, domain, data)
    finally:
        if fetched:   # "the API answered", not "something changed": a parked car sends no new events for hours
            db.execute("INSERT INTO poll_status VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET last_ok = excluded.last_ok",
                       (time.time(),))
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
        except (httpx.HTTPError, sqlite3.Error, KeyError, ValueError) as e:   # keep polling through a bad cycle
            print(stamp, "error:", type(e).__name__, flush=True)
        if args.once:
            break
        time.sleep(active if is_active(db) else idle)


if __name__ == "__main__":
    main()
