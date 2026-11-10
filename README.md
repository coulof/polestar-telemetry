# Polestar telemetry

Collect, store and understand your Polestar 2 usage: trips, charging, battery, cabin air and a weekly written summary, in a small self-hosted dashboard.

The [Polestar Data Portal API](api.json) only returns the **current state** of the car. It has no history and no trips. This app polls it, keeps every snapshot in SQLite, derives trips and charge sessions from those snapshots, and shows them in a Streamlit dashboard.

```
Polestar API --> polestar/collect.py --> data/telemetry.db --> derive / insights / stats --> app.py (Streamlit)
                                                                                     \--> report.py --> claude -p --> Report tab
```

Python 3.13, SQLite, Streamlit, Plotly. Runs on a laptop for development and in two containers (collector and dashboard) on a small server.

## Quick start

Requires [mise](https://mise.jdx.dev), which installs Python and [uv](https://docs.astral.sh/uv/).

```sh
mise run setup                       # install dependencies
cp config.example.toml config.toml   # optional: poll intervals, priced locations, tariffs
mise run smoke                       # check credentials and endpoints (prints field names only)
mise run collect                     # poll once and store a snapshot
mise run watch                       # poll in a loop (60 s while charging or driving, 300 s otherwise)
mise run app                         # dashboard on http://localhost:8501
mise run test
```

### Credentials (`.env`, never committed)

| Variable | Meaning |
|---|---|
| `M2M_ENDPOINT` | Token URL (may end in `/token`) |
| `APP_ID` | API client ID |
| `SECRET` | API client secret |
| `ACCOUNT_ID` | Your account ID, sent as the `x-client-id` header (differs from `APP_ID`) |

Optional environment variables: `DB_PATH` (default `data/telemetry.db`), `CONFIG_PATH` (default `config.toml`), `BATTERY_KWH` (usable capacity used for energy estimates, default 78, unverified), `CLAUDE_BIN` (path of the `claude` CLI for the weekly report), `POLESTAR_MOCK=1` (show the design mock-up tabs on synthetic data).

## Configuration (`config.toml`)

Personal and gitignored; copy [`config.example.toml`](config.example.toml). It is re-read on every poll, so edits apply without a restart.

- **Polling:** `[poll] interval` and `interval_active` are the seconds between polls when idle and while charging or driving (defaults 300 and 60). The collector flags `--interval` and `--interval-active` take precedence.
- **Locations and prices:** one `[[location]]` per place with `name`, `lat`, `lon`, `radius_m` and `price_per_kwh`. A charge session is priced by the position the car last reported before it started.
  - Optional `[[location.tariff]]` sub-tables give time-of-use pricing: `from`, `to`, `price_per_kwh` and an optional `days` list such as `["sat", "sun"]`. Windows may cross midnight, and `from = to` means the whole day. Each reading interval of a session is priced separately.
  - Optional `session_fee` is a fixed amount added once to each charging session at that place.
  - `[pricing] default_price_per_kwh` covers charges away from any listed place. When unset, the cost there is unknown.
- **Clock:** a top-level `timezone` (default `Europe/Paris`) sets the time shown in the dashboard and the clock for tariffs and weeks. The server containers run in UTC, so this matters.
- **Other tables:** `[report]` has `timezone` and `model` (Claude Code model alias). `[service]` has `warn_days` and `warn_km` for the service-due warning. `[co2]` has `g_per_kwh`, which enables CO2 figures in Insights.

## What the dashboard shows

- **Now:** charge gauge with low and high zones, range, a status chip (Charging, Driving, Parked) that pulses while active, energy and cost so far when charging, lock and service badges, last position with its age. Charging notes appear only for exceptions: plugged in but not charging, a charger fault, or charging at a peak rate while a cheaper window is coming.
- **Report:** a weekly "hot takes" summary written by Claude from the week's aggregated stats, with the numbers behind it.
- **Trips:** distance per day, estimated consumption (kWh/100 km) and a table. A trip is one driving window.
- **Charging:** sessions with a power curve, energy and cost by location, "could have saved" (peak-rate energy priced at the cheapest rate), a warning when sessions run below the car's amp limit, and the car's charging settings (target charge, amp limit, timers, read-only).
- **Battery:** state of charge over time with charging sessions shaded, and SoC lost while parked.
- **Insights:** real-world range, energy per full charge, cost per 100 km, optional CO2, a driving, charging and parked timeline, and a monthly table.
- **Air:** cabin air quality index and PM2.5 over time, a daily summary and pre-cleaning cycles. The API documents neither the index scale nor the PM2.5 unit.
- **Data:** snapshot counts and freshness per domain, and the latest raw payload.

The **Period** selector in the sidebar filters Trips, Charging, Battery, Insights and Air. Now, Report and Data always show the latest.

## How the data behaves

- Telemetry is **event-driven**: the car pushes an update only when a value changes. The battery reports every few minutes while charging. A poll sees only the latest event, so a shorter interval captures more of them.
- **While driving** the car sends battery events but no position, and the odometer only when a drive starts and when it ends. Trips are therefore the car's driving windows (from usage-mode events) with the distance taken from the odometer at both ends. There is no route to draw, only where the car parked.
- Energy figures are **estimates**: trips use the SoC change times `BATTERY_KWH`, and charge sessions use reported power over time. This car's API exposes no energy counters. Trips under 3 km get no energy estimate because SoC moves in 1 % steps.
- A charge session whose start was not observed (it began before tracking, or after a long gap) is marked "since tracking began" and its energy is a lower bound.
- History starts at the first poll. There is no backfill.
- The dashboard has **no authentication** and shows your location. Keep it on localhost or a private network such as Tailscale.

## Deploy and operate

`Dockerfile` and `docker-compose.yml` run two services that share a volume: `collector` (polls the API) and `app` (the dashboard). `config.toml` and `.env` must exist next to the compose file. Podman and Docker both work.

```sh
BIND_ADDR=<private-ip> podman-compose up -d --build     # or: docker compose up -d --build
```

`BIND_ADDR` defaults to `127.0.0.1`. Set it to a private interface address (for example a Tailscale IP) to reach the dashboard from other devices.

### Keep it running

With rootless podman, enable linger (`loginctl enable-linger <user>`) and install [`deploy/polestar-telemetry.service`](deploy/polestar-telemetry.service) as a systemd user unit. It waits for the private interface, then runs `podman-compose up -d` at boot.

### Update

On the host, in a git clone of this repository:

```sh
deploy/update.sh      # runs the tests, git pull --ff-only, rebuilds the images, restarts, checks health
```

`config.toml` and `.env` are not in git. Keep them in the project folder on the host and edit them there.

### Weekly report (Claude Code CLI)

The report is written by the Claude Code CLI (`claude -p`, your subscription login, no API key). The container has no CLI, so a host-side timer runs [`deploy/weekly-report.sh`](deploy/weekly-report.sh): it asks the collector container for the prompt, pipes it to `claude` (text only: no tools, skills or MCP), and stores the answer through the container. Only aggregated figures are sent, never the VIN or coordinates. Install on the host, which needs the `claude` CLI logged in:

```sh
cp deploy/polestar-report.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now polestar-report.timer   # Mondays 08:15
deploy/weekly-report.sh [ISO-WEEK]    # run it now; FORCE=1 replaces an existing report
mise run report                       # or locally, against the local database
```

If a run fails (for example the CLI login expired) the Report tab keeps showing the previous report. The cause is in `journalctl --user -u polestar-report`.

### Troubleshooting

- Logs: `podman logs -f polestar-telemetry_collector_1` (one line per poll with the number of new rows per domain) and `..._app_1`.
- A domain returning 404 (saved charge locations on this car) is skipped silently, by design.
- Times one hour off mean `timezone` is missing from `config.toml`.

## Development

```sh
mise run test          # 40+ tests; no network and no real claude CLI
POLESTAR_MOCK=1 mise run app    # adds the "Compare (mock)" and "Path (mock)" design tabs
```

Conventions, data gotchas, brand colours and the server workflow are in [`AGENTS.md`](AGENTS.md), which is written for coding agents and is just as useful for people.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Streamlit dashboard |
| `polestar/client.py` | OAuth token cache and telemetry GETs |
| `polestar/collect.py` | Poll loop, SQLite storage deduped on `metaEventId` |
| `polestar/config.py` | `config.toml` loading, location matching, tariffs |
| `polestar/derive.py` | Trips, charge sessions, parked drain, pricing, air quality |
| `polestar/insights.py` | Real-world range, capacity trend, monthly costs, state timeline |
| `polestar/charging.py` | The car's charging settings, plug-state and amp-limit checks |
| `polestar/stats.py` | Weekly aggregates without VIN or coordinates |
| `polestar/report.py` | Weekly report: prompt, storage, `claude -p` call |
| `polestar/theme.py` | Chart styling, CSS and the status chip |
| `polestar/mock_compare.py`, `polestar/mock_path.py` | Design mock-ups on synthetic data |
| `deploy/` | systemd units, `update.sh`, `weekly-report.sh` |
| `smoke.py` | Credential and endpoint check |
| `tests/` | pytest suite |

## Roadmap

1. Build the real month-to-month **Compare** view and the **Hero's Path** map and timeline. Mock-ups of both exist (`POLESTAR_MOCK=1`); they need a few weeks of real history. The car reports no position while driving, so a real path would be curves between parked positions.
2. Tests for `client.py` and `collect.py` (mocked HTTP).
3. Import local history into the server database.
4. Explore the `request_rvdc` records in the Polestar data export (meaning unverified).
5. Saved charge locations: the API endpoint exists but returns 404 for this car, so there is nothing to import yet.
