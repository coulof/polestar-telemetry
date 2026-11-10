# Polestar telemetry

Collect, store and visualize your Polestar 2 usage via the [Polestar Data Portal](api.json) M2M API.

The API only returns the **current state** of the car (no history, no trips). This app polls it,
stores every snapshot in SQLite, derives trips and charge sessions, and shows them in a Streamlit dashboard.

```
Polestar API --> polestar/collect.py --> data/telemetry.db --> polestar/derive.py --> app.py (Streamlit)
```

## Quick start

Requires [mise](https://mise.jdx.dev) (installs Python and uv).

```sh
mise run setup      # install dependencies
cp config.example.toml config.toml   # optional: intervals + priced locations
mise run smoke      # check credentials and endpoints (prints field names only)
mise run collect    # poll once
mise run watch      # poll in a loop
mise run app        # dashboard on http://localhost:8501
mise run test
```

### Credentials (`.env`, never committed)

| Variable | Meaning |
|---|---|
| `M2M_ENDPOINT` | Token URL (may end in `/token`) |
| `APP_ID` | API client ID |
| `SECRET` | API client secret |
| `ACCOUNT_ID` | Your account ID, sent as the `x-client-id` header (differs from `APP_ID`) |

Optional: `DB_PATH` (default `data/telemetry.db`), `CONFIG_PATH` (default `config.toml`), `BATTERY_KWH` (usable capacity, default 78, unverified).

## Configuration (`config.toml`)

Re-read on every poll, so edits apply without a restart. See [`config.example.toml`](config.example.toml).

- `[poll] interval` / `interval_active`: seconds between polls when idle / while charging or driving (defaults 300 / 60). Command-line flags `--interval` and `--interval-active` take precedence.
- `[[location]]`: name, lat, lon, `radius_m`, `price_per_kwh`. A charge session is priced by the position the car last reported before it started. `[pricing] default_price_per_kwh` covers unlisted places; unset means cost unknown.

## What the dashboard shows

- **Now:** charge, range, charging state and power, lock/open icons, service due, last position.
- **Trips:** distance per day, estimated consumption (kWh/100 km), table.
- **Charging:** sessions, power curve, energy, cost by location.
- **Battery:** state of charge over time, SoC lost while parked.
- **Data:** snapshot counts and freshness per domain, latest raw payload.

## Things to know

- Telemetry is **event-driven**: the car pushes an update only when a value changes (battery about every 2-6 minutes while charging). A poll sees only the latest event, so a shorter interval captures more of them.
- Energy figures are **estimates**: trips use SoC change x `BATTERY_KWH`; charge sessions use reported power over time. This car's API does not expose energy counters.
- History starts at the first poll; there is no backfill.
- The dashboard has **no authentication** and shows your location. Keep it on localhost or a private network (Tailscale).

## Deploy (podman or docker)

`Dockerfile` and `docker-compose.yml` run two services, `collector` and `app`, sharing a volume. `config.toml` and `.env` must exist next to the compose file.

```sh
BIND_ADDR=<private-ip> podman-compose up -d --build     # or: docker compose up -d --build
```

`BIND_ADDR` defaults to `127.0.0.1`. To start at boot with rootless podman, enable linger and install
[`deploy/polestar-telemetry.service`](deploy/polestar-telemetry.service) as a systemd user unit (instructions in the file).

## Layout

| Path | Purpose |
|---|---|
| `polestar/client.py` | OAuth token cache + telemetry GETs |
| `polestar/collect.py` | Poll loop, SQLite storage (deduped on `metaEventId`) |
| `polestar/config.py` | `config.toml` loading, location matching |
| `polestar/derive.py` | Trips, charge sessions, parked drain, pricing |
| `app.py` | Streamlit dashboard |
| `smoke.py` | Credential / endpoint check |
| `tests/` | pytest suite for `derive` and `config` |

## Roadmap

1. **Air quality report** (next): the `preCleaning` domain already collects the cabin air quality index, PM2.5 and last pre-cleaning cycle. Add a dashboard tab with trends over time, a daily summary and pre-cleaning cycle history.
2. Calibrate `BATTERY_KWH` from a complete charge session (`kwh_from_power` / SoC change).
3. Settings view: target SoC, amp limit and charge timers; flag when charging power is below the configured limit.
4. Tests for `client.py` and `collect.py` (mocked HTTP).
5. Import local history into the server database.
6. Explore the `request_rvdc` records in the Polestar data export (meaning unverified).
