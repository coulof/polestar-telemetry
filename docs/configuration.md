# Configuration

## Credentials (`.env`, never committed)

Register for API access on the [Polestar Data Portal](https://data-portal.polestar.com/) (a developer account under the EU Data Act, tied to a Polestar ID that is linked to your car; [terms](https://www.polestar.com/ie/legal/terms-conditions/data-portal)). Create a credential there. It gives you three values, which go into `.env`:

It gives you three values: Client ID, Client secret and Account ID. They go into `.env` as `APP_ID`, `SECRET` and `ACCOUNT_ID` (table below).

`M2M_ENDPOINT` is the token URL of the API's server, `https://pc-api.polestar.com/eu-north-1/data-portal/m2m/token` (see [`api.json`](../api.json)). The app asks for the read-only `pdp-telemetry/*` and `pdp-charging/*` scopes on its own, so there is nothing to select. Keep the secret out of git (`.env` is ignored) and treat it like a password. [evcc](https://docs.evcc.io/en/vehicles/polestar) documents the same three values.

| Variable | Portal label | Meaning |
|---|---|---|
| `M2M_ENDPOINT` | | Token URL (may end in `/token`) |
| `APP_ID` | Client ID | API client ID |
| `SECRET` | Client secret | API client secret |
| `ACCOUNT_ID` | Account ID | Sent as the `x-client-id` header (differs from `APP_ID`) |
| `DASHBOARD_PASSWORD` | | Optional login page for the whole dashboard, see [Access](deployment.md#access-and-the-login-page) |
| `ADMIN_PASSWORD` | | Optional. Unlocks the few things the dashboard can change (manual charge costs); without it editing is off. Generate one with `openssl rand -base64 18` |

Optional environment variables: `DB_PATH` (default `data/telemetry.db`), `CONFIG_PATH` (default `config.toml`), `BATTERY_KWH` (usable capacity used for energy estimates, default 78, unverified), `BIND_ADDR` (address the dashboard listens on, default `127.0.0.1`), `CLAUDE_BIN` (path of the `claude` CLI for the weekly report), `NOTIFY_URL` (push failure messages, see [Weekly report](deployment.md#weekly-report-claude-code-cli)), `BACKUP_DIR` and `KEEP` (where `deploy/backup.sh` writes and how many copies it keeps), `COLLECTOR` (container name the host scripts use), `POLESTAR_MOCK=1` (show the design mock-up tabs on synthetic data).

## `config.toml`

Personal and gitignored; copy [`config.example.toml`](../config.example.toml). It is re-read on every poll, so edits apply without a restart.

- **Polling:** `[poll] interval` and `interval_active` are the seconds between polls when idle and while charging or driving (defaults 300 and 60). The collector flags `--interval` and `--interval-active` take precedence.
- **Locations and prices:** one `[[location]]` per place with `name`, `lat`, `lon`, `radius_m` and `price_per_kwh`. A charge session is priced by the position the car last reported before it started.
  - Optional `[[location.tariff]]` sub-tables give time-of-use pricing: `from`, `to`, `price_per_kwh` and an optional `days` list such as `["sat", "sun"]`. Windows may cross midnight, and `from = to` means the whole day. Each reading interval of a session is priced separately.
  - Optional `session_fee` is a fixed amount added once to each charging session at that place.
  - `[pricing] default_price_per_kwh` covers charges away from any listed place. When unset, the cost there is unknown.
- **Clock:** a top-level `timezone` (default `Europe/Paris`) sets the time shown in the dashboard and the clock for tariffs and weeks. The server containers run in UTC, so this matters.
- **Other tables:** `[report]` has `enabled` (default `true`; `false` hides the Report tab and makes the weekly report script exit without calling Claude), `timezone` and `model` (Claude Code model alias). `[service]` has `warn_days` and `warn_km` for the service-due warning. `[co2]` has `g_per_kwh`, which enables CO2 figures in Insights.
