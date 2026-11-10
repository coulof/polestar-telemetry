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
- `[[location]]`: name, lat, lon, `radius_m`, `price_per_kwh`. A charge session is priced by the position the car last reported before it started. Optional `[[location.tariff]]` sub-tables (`from`, `to`, `price_per_kwh`, optional `days`; windows may cross midnight, `from` = `to` means all day) give time-of-use pricing, applied per reading interval. Top-level `timezone` sets the clock for tariffs and weeks. `[pricing] default_price_per_kwh` covers unlisted places; unset means cost unknown.
- `[report]` `timezone` (weeks run Monday to Sunday) and `model` (Claude Code model alias). `[service]` `warn_days` / `warn_km`: when the service-due warning appears. `[co2] g_per_kwh`: grid carbon intensity, enables CO2 figures in Insights.

## What the dashboard shows

- **Now:** charge gauge with low/high zones, range, charging power, energy and cost so far, lock/open and service badges, last position. Charging notes appear only for exceptions: plugged in but not charging, a charger fault, or charging at a peak rate while a cheaper window is coming.
- **Report:** a weekly "hot takes" summary written by Claude from the week's aggregated stats, with the numbers behind it.
- **Trips:** distance per day, estimated consumption (kWh/100 km), table.
- **Charging:** sessions, power curve, energy, cost by location, "could have saved" (energy charged at peak rates priced at the cheapest rate, needs time-of-use tariffs), a warning when sessions run below the car's amp limit, and the car's charging settings (target charge, amp limit, timers; read-only).
- **Battery:** state of charge over time, SoC lost while parked.
- **Insights:** real-world range, energy per full charge, cost per 100 km, optional CO2, a driving/charging/parked timeline and a monthly table.
- **Air:** cabin air quality index and PM2.5 over time, daily summary, pre-cleaning cycles (the API documents neither the index scale nor the PM2.5 unit).
- **Data:** snapshot counts and freshness per domain, latest raw payload.

## Things to know

- Telemetry is **event-driven**: the car pushes an update only when a value changes (battery about every 2-6 minutes while charging). A poll sees only the latest event, so a shorter interval captures more of them.
- **Trips** are the car's driving windows (from usage-mode events) with the distance taken from the odometer at the start and end, because the car reports the odometer only at those two moments and no position while driving.
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

### Weekly report (Claude Code CLI)

The report is written by the Claude Code CLI (`claude -p`, your subscription login, no API key). The container has no CLI, so a host-side timer runs [`deploy/weekly-report.sh`](deploy/weekly-report.sh): it asks the collector container for the prompt, pipes it to `claude` (text only: no tools, skills or MCP), and stores the answer through the container. Only aggregated figures are sent. Install on the host (needs the `claude` CLI logged in):

```sh
cp deploy/polestar-report.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now polestar-report.timer   # Mondays 08:15
deploy/weekly-report.sh [ISO-WEEK]    # run it now; FORCE=1 replaces an existing report
mise run report                       # or locally, against the local database
```

If a run fails (for example the CLI login expired) the Report tab keeps showing the previous report.

## Layout

| Path | Purpose |
|---|---|
| `polestar/client.py` | OAuth token cache + telemetry GETs |
| `polestar/collect.py` | Poll loop, SQLite storage (deduped on `metaEventId`) |
| `polestar/config.py` | `config.toml` loading, location matching |
| `polestar/derive.py` | Trips, charge sessions, parked drain, pricing, air quality |
| `polestar/stats.py` | Weekly aggregates (no VIN or coordinates) |
| `polestar/report.py` | Weekly report: prompt, storage, `claude -p` call |
| `polestar/charging.py` | Car charging settings, plug-state and amp-limit exception checks |
| `polestar/insights.py` | Real-world range, capacity trend, monthly costs, state timeline |
| `app.py` | Streamlit dashboard |
| `smoke.py` | Credential / endpoint check |
| `tests/` | pytest suite for `derive` and `config` |

## Roadmap

1. Tests for `client.py` and `collect.py` (mocked HTTP).
2. Import local history into the server database.
3. Explore the `request_rvdc` records in the Polestar data export (meaning unverified).
4. Saved charge locations: the API endpoint exists, but it returns 404 for this car, so there is nothing to import yet.
