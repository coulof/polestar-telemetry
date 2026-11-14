# Development

```sh
mise run test          # the whole suite; no network and no real claude CLI
POLESTAR_MOCK=1 mise run app    # adds the "Compare (mock)" and "Path (mock)" design tabs
```

Regenerate the README images from a synthetic database (never real data):

```sh
uv run python demo/make_demo_db.py data/demo.db
DB_PATH=data/demo.db CONFIG_PATH=demo/config.toml uv run python -m polestar.report --generate --week <last ISO week>   # optional: needs claude
DB_PATH=data/demo.db CONFIG_PATH=demo/config.toml .venv/bin/python -m streamlit run app.py --server.port 8599 &
uv run --no-project --with playwright python demo/capture.py http://localhost:8599 docs/img   # CHROME=/path/to/chrome if needed
```

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
| `polestar/insights.py` | Real-world range, capacity trend, monthly costs, state timeline |
| `polestar/charging.py` | The car's charging settings, plug-state and amp-limit checks |
| `polestar/stats.py` | Weekly aggregates without VIN or coordinates |
| `polestar/report.py` | Weekly report: prompt, storage, `claude -p` call |
| `polestar/admin.py` | Dashboard and admin passwords: sessions and lockout |
| `polestar/freshness.py` | Age of the collector's last successful poll, for the stale-data alert |
| `polestar/costs.py` | Manual charge costs stored per session |
| `polestar/theme.py` | Chart styling, CSS and the status chip |
| `polestar/mock_compare.py`, `polestar/mock_path.py` | Design mock-ups on synthetic data |
| `deploy/` | systemd units and timers (stack, report, backup), `update.sh`, `weekly-report.sh`, `backup.sh`, `freshness.sh`, `notify.sh` |
| `static/` | Self-hosted Inter font and its licence (`OFL.txt`, must stay next to the font) |
| `demo/` | Synthetic database and screenshot capture for the README images |
| `docs/img/` | README screenshots and tour |
| `.github/workflows/ci.yml`, `.github/dependabot.yml` | Lint, tests and a Docker image build with a health check on every push; dependency update PRs |
| `LICENSE` | MIT |
| `smoke.py` | Credential and endpoint check |
| `tests/` | pytest suite (no network, no real `claude`), including a headless dashboard run and the host scripts against fake `podman` and `claude` |
