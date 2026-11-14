# Deployment and operation

## One-command deploy

With `.env` in place (see [Credentials](configuration.md#credentials-env-never-committed)), one command brings everything up:

```sh
mise run deploy
```

It runs three tasks in order, and you can run any of them on its own:

| Task | What it does |
|---|---|
| `deploy:check` | Checks `.env`, creates `config.toml` from `config.example.toml` if missing, checks for `podman-compose` or `docker compose` |
| `deploy:stack` | Builds and starts the collector and dashboard, waits for the health check (`http://127.0.0.1:8501`, or `BIND_ADDR`) |
| `deploy:claude` | Checks the `claude` CLI is installed and logged in, installs the weekly report timer (Mondays 08:15) for this folder. Needs podman. Skipped, with no `claude` login needed, when `config.toml` has `[report] enabled = false` |

With Docker you get the stack but not the report timer, and `deploy/update.sh` and the systemd units assume podman. The sections below cover the same steps by hand.

`Dockerfile` and `docker-compose.yml` run two services that share a volume: `collector` (polls the API) and `app` (the dashboard). `config.toml` and `.env` must exist next to the compose file. Podman and Docker both work.

```sh
BIND_ADDR=<private-ip> podman-compose up -d --build     # or: docker compose up -d --build
```

`BIND_ADDR` defaults to `127.0.0.1`. Set it to a private interface address (for example a Tailscale IP) to reach the dashboard from other devices.

## Keep it running

With rootless podman, enable linger (`loginctl enable-linger <user>`) and install [`deploy/polestar-telemetry.service`](../deploy/polestar-telemetry.service) as a systemd user unit. It waits for the private interface, then runs `podman-compose up -d` at boot.

## Access and the login page

By default the dashboard listens on `127.0.0.1` only (`BIND_ADDR` changes that). Reaching it from elsewhere means exposing the port, so decide who can see your drives and charging places. `DASHBOARD_PASSWORD` in `.env` adds a login page. Its limits:

- **Scope:** a shared password in front of the whole dashboard. Nothing is read or drawn until it is entered. A session lasts 12 hours, five wrong guesses lock every session for a minute, and an unset password means no login page.
- **Limits:** there are no user accounts, no per-user history and no per-address lockout, so a patient attacker is only slowed down. The password is sent as plain text over HTTP, so anyone on the network path can read it. The Streamlit server itself also answers its health check (`/_stcore/health`) and static files without a login.
- **How to use it safely:** keep the port on localhost or a private network such as Tailscale, or put a TLS reverse proxy (Caddy, nginx) in front and bind the app to localhost. Use a long random password (`openssl rand -base64 18`) that you use nowhere else.
- **When you need more:** accounts or single sign-on need Streamlit's `st.login` with an OpenID Connect provider (Google, Microsoft and so on), or an authenticating proxy; that is not built here.
- **Setting it:** add `DASHBOARD_PASSWORD=<value>` to `.env` (keep it mode 600) without typing the value into shell history, for example `read -rsp 'Password: ' p; printf 'DASHBOARD_PASSWORD=%s\n' "$p" >> .env; unset p`, then restart the stack (`systemctl --user restart polestar-telemetry`, or `podman-compose up -d`). The same works for `ADMIN_PASSWORD`.
- **Editing is separate:** `ADMIN_PASSWORD` still guards manual charge costs, so someone who can read cannot change them.

## Backups

History cannot be re-fetched (the API only returns the current state), so back the database up. [`deploy/backup.sh`](../deploy/backup.sh) takes an online SQLite backup from the collector container, gzips it into `~/polestar-backups` (mode 700) and keeps the newest 14 (`KEEP`, `BACKUP_DIR`). The copies contain the VIN and positions: keep them off shared storage. Nightly at 03:30:

```sh
cp deploy/polestar-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now polestar-backup.timer
deploy/backup.sh      # run it now
```

## Update

On the host, in a git clone of this repository:

```sh
deploy/update.sh      # runs the tests, git pull --ff-only, rebuilds the images, restarts, checks health
```

`config.toml` and `.env` are not in git. Keep them in the project folder on the host and edit them there.

## Weekly report (Claude Code CLI)

The report is written by the Claude Code CLI (`claude -p`, your subscription login, no API key). The container has no CLI, so a host-side timer runs [`deploy/weekly-report.sh`](../deploy/weekly-report.sh): it asks the collector container for the prompt, pipes it to `claude` (text only: no tools, skills or MCP), and stores the answer through the container. Only aggregated figures are sent, never the VIN or coordinates. Install on the host, which needs the `claude` CLI logged in:

```sh
cp deploy/polestar-report.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now polestar-report.timer   # Mondays 08:15
deploy/weekly-report.sh [ISO-WEEK]    # run it now; FORCE=1 replaces an existing report
mise run report                       # or locally, against the local database
```

If a run fails (for example the CLI login expired) the Report tab keeps showing the previous report and adds a warning with the failed step, until a report for that week is stored. The same warning goes to the journal (`journalctl --user -u polestar-report`) and, if you set `NOTIFY_URL`, to your phone: the scripts POST the message to that URL in the [ntfy](https://ntfy.sh) style (title in a header, message as the body). The message holds only the step and exit code, never data. The nightly backup uses the same channel. Set it on the timers' services, for example with a private topic name:

```sh
systemctl --user edit polestar-report    # add:  [Service]  Environment=NOTIFY_URL=https://ntfy.sh/<your-secret-topic>
systemctl --user edit polestar-backup
```

With ntfy.sh the topic name is the only secret, so pick a long random one; your own ntfy server works the same way.

## Troubleshooting

- Logs: `podman logs -f polestar-telemetry_collector_1` (one line per poll with the number of new rows per domain) and `..._app_1`.
- A domain returning 404 (saved charge locations on this car) is skipped silently, by design.
- Times one hour off mean `timezone` is missing from `config.toml`.
- A new install shows an empty dashboard until the first poll has stored data (up to the poll interval, 5 minutes by default).
- The login page says "Too many wrong attempts": five wrong passwords lock every session for a minute; wait and retry.
- The Report tab shows a warning that the weekly report failed: read `journalctl --user -u polestar-report` and re-run `deploy/weekly-report.sh`; the usual cause is an expired `claude` login.
