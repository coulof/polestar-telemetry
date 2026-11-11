# AGENTS.md

Guide for coding agents (and people) working on this repository. `README.md` has the product overview and setup.

## Project at a glance

A Python app that polls the Polestar Data Portal API into SQLite, derives trips and charge sessions, and shows them in a Streamlit dashboard. A weekly summary is written by the Claude Code CLI.

```
client.py -> collect.py -> data/telemetry.db -> derive.py / insights.py / charging.py / stats.py -> app.py
                                                                              stats.py -> report.py -> claude -p
```

- Snapshots are stored raw (`snapshots` table, one JSON payload per event, deduped on `metaEventId`); everything else is derived on read. A `reports` table holds the weekly reports.
- Two long-running processes share the SQLite file (collector and dashboard); `collect.connect()` enables WAL.
- Module map: see the Layout table in `README.md`. `app.py` loads data and lays out the tabs; figures, badges and charging notes live in `polestar/views.py`.

## Commands

```sh
mise run setup | smoke | collect | watch | app | report | test    # uv-based, Python 3.13 via mise
mise run deploy                                                    # local stack + weekly report timer (deploy:check, deploy:stack, deploy:claude)
uv run pytest -q                                                   # tests
POLESTAR_MOCK=1 mise run app                                       # design mock-up tabs
```

Search code with `rg`. Run the tests after any change under `polestar/` or `app.py`. Streamlit does not reload imported modules without `watchdog`, so restart the server after editing `polestar/*.py`.

## Rules

- **Secrets and personal data:** never read, print or commit `.env`, `config.toml`, `data/` or `export/` (all gitignored). They hold credentials, the VIN, home coordinates and tariffs. In output show field names or counts, not values. Samples, tests and mock-ups use synthetic data and made-up places. `smoke.py` is safe to run (field names only).
- **Commits:** sign off and GPG-sign (`git commit -s -S`), then check `git log -1 --format='%G?'` (expect `G`) and the `Signed-off-by` trailer. Do not commit or push unless asked. If no signing key is available (this host has none), do not commit unless the owner explicitly asks for unsigned commits; say so and leave the change for a signed commit.
- **Outward actions** (push, deploy, anything that sends data off the machine, installing system packages) only on request. Before staging, check that `git diff --cached --name-only` contains no `.env`, `config.toml`, `data/` or `export/`.
- Smallest change that solves the task; match the surrounding style and comment density; do not add features, abstractions or dead code that were not asked for. Report test failures verbatim.
- Tests never touch the network or the real `claude` CLI: use `CLAUDE_BIN` with a fake script and point `CONFIG_PATH` at a missing file (see `tests/test_report.py`).

## Data and API facts

- **Latest-state, event-driven snapshots.** Never assume a regular time series. `derive.py` joins sparse readings with `merge_asof` through `_asof`, which normalises time zones and resolutions to UTC nanoseconds.
- **Auth:** `x-client-id` is `ACCOUNT_ID`, not `APP_ID`. `M2M_ENDPOINT` already ends in `/token`.
- **Paths:** camelCase domain names map to kebab-case URLs in `PATHS` (`client.py`): `parkingClimatization` to `telemetry/parking-climatization`, `preCleaning` to `telemetry/pre-cleaning`, and the charging domains live under `charging/`. All endpoints are read-only GETs; nothing here can control the car.
- **404 domains:** `chargeLocations` and `isAtChargeLocation` return 404 for this car; the collector skips 404s silently.
- **Trips come from `usageMode` driving windows**, not odometer cadence. Observed on 10 Nov 2026: the car sends the odometer once when a drive starts and once when it ends (no events in between) and no location while driving. `derive.trips(..., availability=...)` takes the distance as the odometer change across the window (end reading: latest up to 10 minutes after the window, never past the next drive's start). Every driving window is its own trip, even two legs 2 minutes apart (the owner confirmed). The odometer-gap method (`TRIP_GAP_S`) is only the fallback without usage-mode data. There is no route, only start and end.
- **Energy is estimated.** `BATTERY_KWH` (78) is unverified and battery energy counters are null on this car. Trips under `MIN_KM_FOR_ENERGY` (3 km) get no energy estimate because SoC moves in 1 % steps; `derive.avg_consumption` averages only trips that have one.
- **Charge sessions:** `partial` marks a session whose start was not observed (energy and SoC change are lower bounds). The session location is the last reported position before it started.
- **Pricing:** `derive.price_sessions(sessions, location, cfg, battery)` returns `cost`, `cost_cheapest`, `saving`, `fee` and `price_per_kwh` (energy price only). Tariff windows are priced per reading interval (pass `battery`); a tariff with `days` only applies on those weekdays, and the cheapest alternative is the best rate available on the same day. A location's `session_fee` is added once per session and does not change `saving`.
- **Manual costs:** `polestar/costs.py` stores a correction per charge session in `session_costs` (keyed by the session start in epoch seconds, total paid in EUR). `derive.price_sessions(..., manual=costs.load(db))` replaces the estimate: no saving, no fee, `manual=True`. Pass `manual` wherever sessions are priced (`app.py`, `stats.py`) so the weekly report agrees with the dashboard.
- **Charging settings** (`targetSoc`, `ampLimit`, timers) are read-only snapshots read through `polestar/charging.py`. Timer times are local at the stored `offsetMinutes`.
- **Time zones:** the stored times are UTC. The dashboard shows `config.load()["timezone"]` (default Europe/Paris), not the machine's zone, because the server containers run in UTC.

## Dashboard conventions

- Streamlit 1.67: use `width="stretch"`, not `use_container_width`.
- **Now tab:** keep it glanceable. Charging notes are for exceptions only (plug problems, charging at a peak rate); do not add always-on lines to the hero card. The status chip shows Driving (gold), Charging (green) or Parked (grey) and pulses while active; it respects `prefers-reduced-motion`.
- **Shape:** sharp and flat, in keeping with the brand: square corners everywhere (`baseRadius = "none"` in `.streamlit/config.toml`, plus `* { border-radius: 0 }` in `theme.CSS`), no card boxes (a bordered container or metric keeps only a top hairline), a square status chip, and small tracked capitals for metric labels. Do not reintroduce rounded cards, pills or rounded bars.
- Chart toolbars are hidden on the gauge; other charts keep Plotly's defaults.
- The sidebar **Period** selector filters Trips, Charging, Battery, Insights and Air; the Battery and Air charts also set their x-axis to the period.
- Mock-ups: `POLESTAR_MOCK=1` adds "Compare (mock)" (`mock_compare.py`, month-to-month) and "Path (mock)" (`mock_path.py`, a map trail with a day scrubber whose camera jumps to each day's trips, a stays timeline and a places table). They use synthetic data, never read the database, and must not ship to the server. Delete the two modules and their hook at the end of `app.py` to remove them.

## Brand and colours

Polestar look, kept calm and uncluttered: one strong accent on a neutral base, tight letter spacing, no decoration. Values come from the brand colour sheet (kept locally in `export/brand-style/`, gitignored) plus the owner's choice of orange.

- **Primary: orange.** `#ED760E` (RAL 2000) on light surfaces, `#FF7500` on dark. Used for the main series, active tab, gauge bar, map marker and the "this period" line. Defined in `polestar/theme.py` (`LIGHT` and `DARK`, key `series`) and `.streamlit/config.toml` (`primaryColor`). Do not redesign around another accent.
- **Secondary: Swedish gold `#F6BE00`** (PMS 7408 C, RAL 1003). Used for secondary encodings: the pulsing "Driving" chip (text `#7A5C00` on light for contrast), the distance and trips heatmaps, and monthly bars (`HUES` in `polestar/mock_compare.py`), ramped from a pale tint up to `#F6BE00` (`#FFCC1A` on dark). Orange and gold are close in hue, so never use them as two adjacent series in one chart; give the comparison series a grey.
- **Neutrals: the brand greys.** Black `#000000`, Grey 11 `#53565A`, Grey 9 `#75787B`, Grey 7 `#97999B`, Grey 5 `#B1B3B3`, Grey 3 `#C8C9C7`, Grey 1 `#D9D9D6`, Grey 0 `#ECECE7`, White `#FFFFFF` (digital only). Used for muted text and grid (`theme.py`), baseline series (Grey 9 on light, Grey 5 on dark), empty heatmap cells (Grey 0) and the card background.
- **Status colours stay separate** from the brand colours (green good, amber or orange warning, red error, blue info) and always come with an icon and a label.
- **Back pocket, not in use:** the brand posters also use a red and a blue. Sampled from the poster images, so approximate and not an official palette: red `#E13C32`, blue `#0028A3`. Keep them for a future third or fourth hue; do not introduce them unless asked.
- **Typography:** the brand face is Unica77 (licensed, unavailable). The app uses Inter, self-hosted in `static/`, as a free stand-in. If a licence appears, swap the font file and the `font` and `fontFaces` names in `.streamlit/config.toml`, plus `FONT` in `polestar/theme.py`.
- **Charts:** follow the `dataviz` skill (thin marks, recessive grid, one hue per metric, a legend for two or more series, a table beside a radar). New colours go through `polestar/theme.py`, not hard-coded in `app.py`.

## Weekly report

`polestar/stats.py` builds the aggregates (never include the VIN or coordinates; a test enforces it). `polestar/report.py` builds the prompt, calls `claude -p` with no tools, skills or MCP from a neutral directory, and stores the result in `reports`. The container has no CLI, so on the server `deploy/weekly-report.sh` runs on the host (a systemd timer, Mondays 08:15) and uses `podman exec` for the stats and storage steps. Failures leave the previous report in place.

## Deployment (same machine)

Development and the running stack live on the same host. Rootless podman with `podman-compose`; containers `polestar-telemetry_collector_1` and `polestar-telemetry_app_1`; the database lives in the named volume `polestar-telemetry_telemetry` (`/data/telemetry.db` inside). The dashboard is bound to `BIND_ADDR` (default `127.0.0.1`; on this host a private Tailscale address set in the systemd unit, `systemctl --user show polestar-telemetry -p Environment`). Boot persistence: the systemd user unit `polestar-telemetry` (linger on), the timers `polestar-report.timer` and `polestar-backup.timer`.

- **The project folder `~/src/polestar-telemetry` is the git clone the stack runs from.** `.env` and `config.toml` live there only (not in git): edit `config.toml` in place (re-read every poll, mode 644 so the container user can read it) and keep `.env` at mode 600.
- **Update flow:** commit and push, then run `deploy/update.sh` in the clone (tests, `git pull --ff-only`, `podman-compose build`, restart the service, health check). `mise run deploy` does the first-time setup. If `uv run pytest` cannot spawn `pytest`, the venv's script shebangs are stale (the folder was moved): `uv sync --reinstall`.
- **Tools on this host:** `claude` is installed and logged in with the owner's subscription, so `mise run report` and `deploy/weekly-report.sh` work. Playwright's Chromium is available for screenshots (`CHROME=<path to chrome>` for `demo/capture.py`). `uv` and `mise` are installed under `~/.local/bin` and `/usr/bin`. There is no GPG key, so commits are unsigned (see Rules). The GitHub CLI is logged in for pushes.
- **Testing against live data without touching it:** copy the database with SQLite's backup API (`podman exec polestar-telemetry_collector_1 python -c "import sqlite3; s=sqlite3.connect('/data/telemetry.db'); d=sqlite3.connect('/tmp/snap.db'); s.backup(d)"`, then `podman cp`), and run the app with `DB_PATH` pointing at the copy. It contains the VIN and positions: keep it out of git and delete it afterwards. `deploy/backup.sh` writes the nightly copies to `~/polestar-backups`.

## Verification checklist

1. `uv run pytest -q` passes (it includes a headless dashboard run on a synthetic database) and `uvx ruff check --select E9,F polestar app.py tests demo smoke.py` is clean; CI runs both.
2. Headless check: `AppTest.from_file("app.py", default_timeout=60).run()` raises no exception (also with `DB_PATH` on a copy of real data when logic over real events changed).
3. For visual changes, screenshot light, dark and a phone width with Playwright (`demo/capture.py` does this on a synthetic database; see "Regenerate the README images" in `README.md`). It needs a Chrome or Chromium.
4. After an update: `curl http://$BIND_ADDR:8501/_stcore/health` returns `ok` (`127.0.0.1` when `BIND_ADDR` is unset), `podman logs --since 3m polestar-telemetry_app_1` shows no traceback, and the collector logs a poll within a minute.
