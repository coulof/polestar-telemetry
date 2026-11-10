#!/usr/bin/env bash
# Update the running stack from git, on the host: pull, test, rebuild, restart, health check.
# Run it inside the project clone. config.toml and .env are not in git and are never touched.
# SKIP_TESTS=1 skips the test run.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

git pull --ff-only
if [ "${SKIP_TESTS:-0}" != 1 ]; then
  uv run --quiet pytest -q    # abort before touching the running containers if anything fails
fi
chmod 644 config.toml       # the container user must be able to read it
chmod 600 .env
podman-compose build
systemctl --user restart polestar-telemetry
systemctl --user is-active polestar-telemetry

addr="$(systemctl --user show polestar-telemetry -p Environment --value | tr ' ' '\n' | sed -n 's/^BIND_ADDR=//p')"
for _ in $(seq 1 30); do
  if curl -fsS "http://${addr:-127.0.0.1}:8501/_stcore/health" >/dev/null 2>&1; then
    echo "healthy"; exit 0
  fi
  sleep 2
done
echo "dashboard did not become healthy" >&2
exit 1
