# Database

One SQLite file (`data/telemetry.db`, `/data/telemetry.db` in the containers), in WAL mode so the collector can write while the dashboard reads. The schema is the `SCHEMA` string in `polestar/collect.py`; `collect.connect()` creates anything missing with `CREATE ... IF NOT EXISTS`, so an existing database picks up a new table the next time it is opened.

Raw snapshots are the only record of the car. Trips, charge sessions, places, prices and the Path are **derived on read** and never stored.

```mermaid
erDiagram
    snapshots {
        TEXT vin PK "one car per VIN"
        TEXT domain PK "battery, odometer, location, ..."
        TEXT event_id PK "metaEventId, or a hash of the payload"
        REAL ts "vehicle event time, epoch seconds UTC"
        TEXT received_at
        TEXT payload "the raw JSON event"
    }
    readings {
        TEXT vin
        REAL ts
        REAL soc_pct
        REAL range_km
        TEXT charging_status
        TEXT charging_type
        REAL charging_power_w
        REAL charging_current_a
        REAL avg_kwh_100km
    }
    poll_status {
        INTEGER id PK "always 1"
        REAL last_ok "epoch seconds of the last successful poll"
    }
    reports {
        TEXT week PK "ISO week, 2026-W46"
        TEXT created_at
        TEXT model
        TEXT markdown "the summary for a week, month, quarter or year"
        TEXT stats "aggregated JSON it was written from"
    }
    report_failures {
        TEXT week PK "ISO week without a report"
        TEXT failed_at
        TEXT error "the failed step, never data"
    }
    session_costs {
        INTEGER start_ts PK "charge session start, epoch seconds UTC"
        REAL cost "EUR paid, replaces the estimate"
        TEXT updated_at
    }
    session_locations {
        INTEGER start_ts PK "charge session start, epoch seconds UTC"
        TEXT location "config.toml location name or any label"
        TEXT updated_at
    }
    place_labels {
        TEXT seed PK "first parked position, lat,lon to 4 decimals"
        TEXT label "name shown on the Path tab"
        TEXT updated_at
    }

    snapshots ||--o{ readings : "view: domain = battery"
    reports |o..o| report_failures : "same week; a stored report clears the failure"
    snapshots }o..o| session_costs : "derived charge session starts at start_ts"
    snapshots }o..o| session_locations : "derived charge session starts at start_ts"
    snapshots }o..o| place_labels : "derived place starts at seed"
```

Solid lines are real (the `readings` view selects from `snapshots`). Dotted lines are conventions kept by the code, not foreign keys: SQLite is not asked to enforce them.

## Tables

| Table | Written by | Holds |
|---|---|---|
| `snapshots` | collector (`collect.store`) | One row per telemetry event, the payload untouched. Deduplicated on `(vin, domain, event_id)`, so a poll that sees nothing new inserts nothing. Indexed on `(vin, domain, ts)`. |
| `poll_status` | collector (`collect_once`) | A single row: the time of the last poll that fetched data. Liveness comes from this, not from the newest event, because a parked car sends nothing for hours. |
| `reports` | `deploy/report.sh` (`report.store`, `report.store_grade`) | The Claude summaries, one per period id (`week` column: `2026-W47`, `2026-11`, `2026-Q4` or `2026`), the aggregates each was written from, and Claude's grade (`score` 1-10, `score_note`). No VIN or coordinates. |
| `report_failures` | the same script (`report.record_failure`) | A week whose report could not be written; shown as a warning on the Report tab until that week has a report. |
| `session_costs` | dashboard, behind the admin password (`costs.py`) | What you actually paid for a charge session, which replaces the tariff estimate. |
| `session_locations` | dashboard, behind the admin password (`costs.py`) | Where a charge session really was, which replaces the detected place: a `config.toml` location name (priced with its tariff and fee) or any other label (priced at the default price). |
| `place_labels` | dashboard, behind the admin password (`place_labels.py`) | A name for a place the Path tab grouped automatically. |

`readings` is a convenience view over the battery snapshots for ad-hoc SQL. The dashboard does not use it.

## Snapshot domains

`snapshots.domain` is one of the API domains in `polestar/client.py`:

- **Telemetry:** `battery`, `odometer`, `location`, `availability` (usage mode), `exterior`, `health`, `parkingClimatization`, `preCleaning` (cabin air).
- **Charging:** `targetSoc`, `ampLimit`, `globalChargeTimer`, `parkingClimateTimer`, plus `chargeLocations` and `isAtChargeLocation`, which return 404 for this car and so store nothing.

`derive.load(db, domain)` turns one domain's payloads into a DataFrame (nested keys flattened, `time` in UTC), which every derived view starts from.

## Keys that outlive the data

- **`session_costs.start_ts`** and **`session_locations.start_ts`** are the charge session's start as whole epoch seconds. Sessions are rebuilt from snapshots each time, so the start is the only stable identity they have. If a late event moved a session's start, its corrections would no longer match.
- **`place_labels.seed`** is the rounded position of a place's first reading. The automatic "Place 1", "Place 2" numbers move as history grows, but the first reading of a place never changes.

## Privacy

`snapshots` holds the VIN and, in `location` payloads, real positions. `place_labels.seed` holds rounded coordinates. Treat the file and its backups (`~/polestar-backups`) as personal data: never commit them, and see [Deployment](deployment.md#backups) for the nightly copies.
