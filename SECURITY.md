# Security

## What to know before you expose this

- The dashboard shows your trips, charging places and car state. It has **no login by default**: set `DASHBOARD_PASSWORD` (and `ADMIN_PASSWORD` to allow editing charge costs), and keep the port on localhost or a private network such as Tailscale. The login is one shared password over plain HTTP; use TLS or a private network. See [Access and the login page](docs/deployment.md#access-and-the-login-page).
- `.env` holds your Polestar API credentials. Keep it out of git (it is ignored) and at mode 600. The app only uses read-only API scopes and cannot control the car.
- The database and its backups contain the VIN and positions. Do not share them.
- Weekly-report prompts contain aggregated figures only, never the VIN or coordinates (a test enforces this).

## Reporting a vulnerability

Please report it privately through GitHub's "Report a vulnerability" button on the repository's Security tab, not in a public issue. This is a personal project: expect a reply within a few days, not an SLA.
