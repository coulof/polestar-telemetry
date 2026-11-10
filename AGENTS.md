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
- Charging settings (`targetSoc`, `ampLimit`, timers) are read-only snapshots via `polestar/charging.py`. `chargeLocations` and `isAtChargeLocation` return 404 for this car; the collector skips 404s silently. Timer times are local at the stored `offsetMinutes`.
- Time-of-use tariffs are priced per reading interval in `derive.price_sessions` (pass `battery`); it also returns `cost_cheapest` and `saving` (peak energy at the cheapest rate). The Now tab shows charging notes for exceptions only; avoid adding always-on lines to the hero card.
- Several processes share the SQLite file; `connect()` enables WAL.

## Brand and colours

Polestar look, kept calm and uncluttered: one strong accent on a neutral base, tight letter spacing, no decoration. Palette values come from the brand colour sheet (kept locally in `export/brand-style/`, gitignored) plus the owner's choice of orange.

- **Primary: orange.** `#ED760E` (RAL 2000) on light surfaces, `#FF7500` on dark. Used for the main series, active tab, gauge bar, map marker and the "this period" line. Defined in `polestar/theme.py` (`LIGHT`/`DARK` `series`) and `.streamlit/config.toml` (`primaryColor`). Do not redesign around another accent.
- **Secondary: Swedish gold `#F6BE00`** (PMS 7408 C, RAL 1003). Used for secondary encodings such as the pulsing "Driving" chip (text `#7A5C00` on light for contrast) and the distance/trips heatmaps and monthly bars (`HUES` in `polestar/mock_compare.py`), ramped from a pale tint up to `#F6BE00` (`#FFCC1A` on dark). Orange and gold are close in hue, so never use them as two adjacent series in one chart; give the comparison series a grey.
- **Neutrals: the brand greys.** Black `#000000`, Grey 11 `#53565A`, Grey 9 `#75787B`, Grey 7 `#97999B`, Grey 5 `#B1B3B3`, Grey 3 `#C8C9C7`, Grey 1 `#D9D9D6`, Grey 0 `#ECECE7`, White `#FFFFFF` (digital only). Used for muted text and grid (`theme.py`), baseline/comparison series (Grey 9 on light, Grey 5 on dark), empty heatmap cells (Grey 0) and the card background.
- **Status colours stay separate** from the brand colours (green good, amber/orange warning, red error, blue info) and always come with an icon and a label.
- **Back pocket, not in use yet:** the brand posters also use a red and a blue. Sampled from the poster images (approximate, not an official palette): red `#E13C32`, blue `#0028A3`. Keep them available for a future third or fourth hue; do not introduce them without being asked.
- **Typography:** the brand face is Unica77 (licensed, not available). The app uses Inter, self-hosted in `static/`, as a free stand-in; swap the font file and the `font`/`fontFaces` names in `.streamlit/config.toml` plus `FONT` in `polestar/theme.py` if a licence appears.
- **Charts:** follow the `dataviz` skill (thin marks, recessive grid, one hue per metric, legend for two or more series, tables beside radars). New colours go through `polestar/theme.py`, not hard-coded in `app.py`.
- **Mock-ups:** `POLESTAR_MOCK=1` shows a "Compare (mock)" tab built on synthetic data (`polestar/mock_compare.py`). It never ships to the server and does not read the database.

## Weekly report

`polestar/stats.py` builds the aggregates (never include VIN or coordinates; there is a test for it), `polestar/report.py` builds the prompt, calls `claude -p` (subscription login, no API key) and stores results in the `reports` table. The container has no CLI: on the server `deploy/weekly-report.sh` runs on the host and uses `podman exec` for the stats and storage steps. Tests must never call the real CLI (use `CLAUDE_BIN` with a fake script, and `CONFIG_PATH` pointing at a missing file).

## Deployment (personal server)

Podman rootless with `podman-compose`, code in `~/src/polestar-telemetry` on the target host, managed by the systemd user unit `polestar-telemetry` (linger enabled). Update flow: rsync the project (excluding `data`, `.venv`, `.git`, `export`), `podman-compose build`, `systemctl --user restart polestar-telemetry`. `config.toml` must be world-readable (mode 644) so the container user can read it; `.env` stays 600.
