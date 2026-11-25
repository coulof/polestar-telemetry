# Development

```sh
mise run test          # the whole suite; no network and no real claude CLI
POLESTAR_MOCK=1 mise run app    # adds the "Compare (mock)" design tab
```

Regenerate the README images from a synthetic database (never real data):

```sh
uv run python demo/make_demo_db.py data/demo.db    # also stores a hand-written sample report for the last two weeks
DB_PATH=data/demo.db CONFIG_PATH=demo/config.toml uv run python -m polestar.report --generate --week <last ISO week>   # optional: a real report instead (needs claude)
DB_PATH=data/demo.db CONFIG_PATH=demo/config.toml .venv/bin/python -m streamlit run app.py --server.port 8599 &
uv run --no-project --with playwright python demo/capture.py http://localhost:8599 docs/img   # CHROME=/path/to/chrome if needed
```

`capture.py` also records the tour and, when `ffmpeg` is installed, turns it into `docs/img/tour.gif` (the page load is trimmed off).

Conventions, data gotchas, brand colours and the server workflow are in [`AGENTS.md`](../AGENTS.md), which is written for coding agents and is just as useful for people.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Streamlit dashboard: sidebar, data loading and the tabs |
| `polestar/views.py` | Plotly figures, status badges and charging notes used by the tabs |
| `polestar/client.py` | OAuth token cache and telemetry GETs |
| `polestar/collect.py` | Poll loop, SQLite storage deduped on `metaEventId` |
| `polestar/config.py` | `config.toml` loading, location matching, tariffs |
| `polestar/derive.py` | Trips, charge sessions, parked drain, pricing, air quality |
| `polestar/insights.py` | Real-world range, capacity trend, monthly costs, activity grid |
| `polestar/charging.py` | The car's charging settings, plug-state and amp-limit checks |
| `polestar/stats.py` | Period ids and aggregates (week, month, quarter, year) without VIN or coordinates |
| `polestar/report.py` | Claude reports: prompt, storage, due periods, `claude -p` call |
| `polestar/admin.py` | Dashboard and admin passwords: sessions and lockout |
| `polestar/freshness.py` | Age of the collector's last successful poll, for the stale-data alert |
| `polestar/costs.py` | Manual charge costs stored per session |
| `polestar/theme.py` | Chart styling, CSS and the status chip |
| `polestar/path.py` | Path tab: parked places, trip stops, trail map, stays timeline |
| `polestar/mock_compare.py` | Design mock-up on synthetic data |
| `deploy/` | systemd units and timers (stack, report, backup), `update.sh`, `report.sh`, `backup.sh`, `freshness.sh`, `notify.sh` |
| `static/` | Self-hosted Inter font and its licence (`OFL.txt`, must stay next to the font) |
| `demo/` | Synthetic database and screenshot capture for the README images |
| `docs/img/` | README screenshots and tour |
| `.github/workflows/ci.yml`, `.github/dependabot.yml` | Lint, tests and a Docker image build with a health check on every push; dependency update PRs |
| `LICENSE` | MIT |
| `smoke.py` | Credential and endpoint check |
| `tests/` | pytest suite (no network, no real `claude`), including a headless dashboard run and the host scripts against fake `podman` and `claude` |

## Public demo

`demo/Dockerfile` builds the real dashboard on the synthetic database from `demo/make_demo_db.py`, with the clock frozen by libfaketime (`DEMO_NOW`, default 2026-11-17 12:00 UTC; wall time frozen, monotonic time real) so the "now" state never goes stale. The image sets `POLESTAR_DEMO=1`, which shows a ribbon linking to the GitHub quick start (`POLESTAR_DEMO_URL` overrides the link). The image holds no real data or secrets and leaves `ADMIN_PASSWORD` and `DASHBOARD_PASSWORD` unset (editing off). The root filesystem is read-only; the database is copied to a tmpfs `/tmp` at start.

```sh
mise run demo:build     # image only
mise run demo:up        # the container on http://127.0.0.1:8599
mise run demo:tunnel    # Cloudflare Tunnel on the host (wrangler tunnel run, uses your wrangler login)
```

To publish at `polestar.lafabrique.ai`: create the tunnel once (`npx wrangler tunnel create polestar-demo`), add the public hostname in Cloudflare Zero Trust (Networks, Tunnels) with service `http://127.0.0.1:8599`, and put the tunnel's ID in the `demo:tunnel` task and in `deploy/polestar-demo-tunnel.service`. For boot persistence install `deploy/polestar-demo.service` and `deploy/polestar-demo-tunnel.service` like the other user units. Consider a rate-limiting rule on the hostname.
