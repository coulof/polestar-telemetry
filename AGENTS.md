# AGENTS.md

Python app that polls the Polestar Data Portal API into SQLite and shows trips, charging and battery data in Streamlit. See `README.md` for the overview.

## Commands

```sh
mise run setup | smoke | collect | watch | app | test    # uv-based; Python 3.13 via mise
uv run pytest                                              # tests (derive, config)
```

Search code with `rg`. Run tests after any change to `polestar/`.

## Rules

- **Secrets and personal data:** never read, print or commit `.env`, `config.toml`, `data/` or `export/` (all gitignored). They hold credentials, the VIN and home coordinates. In output, show field names or counts, not values. `smoke.py` is safe to run.
- **Commits:** sign off and GPG-sign (`git commit -s -S`), then check `git log -1 --format='%G?'` and the `Signed-off-by` trailer. Do not commit or push unless asked.
- **Deploying or pushing** is outward-facing: only on request.
- Smallest change that solves the task; match existing style; report test failures verbatim.

## Gotchas

- API data is a **latest-state snapshot**, event-driven. Never assume a regular time series; `derive.py` handles sparse readings with `merge_asof` (via `_asof`, which normalizes timezones/units to UTC ns).
- `x-client-id` is `ACCOUNT_ID`, not `APP_ID`. `M2M_ENDPOINT` already ends in `/token`.
- The domain name `parkingClimatization` has the URL path `parking-climatization` (and `preCleaning` -> `pre-cleaning`); see `PATHS` in `client.py`.
- Energy is estimated: `BATTERY_KWH` (78) is unverified; battery energy counters are null for this car.
- The dashboard has no auth. The compose file binds it to `BIND_ADDR` (default `127.0.0.1`).
- Streamlit: use `width="stretch"` (not `use_container_width`).
- Several processes share the SQLite file; `connect()` enables WAL.

## Deployment (personal server)

Podman rootless with `podman-compose`, code in `~/src/polestar-telemetry` on the target host, managed by the systemd user unit `polestar-telemetry` (linger enabled). Update flow: rsync the project (excluding `data`, `.venv`, `.git`, `export`), `podman-compose build`, `systemctl --user restart polestar-telemetry`. `config.toml` must be world-readable (mode 644) so the container user can read it; `.env` stays 600.
