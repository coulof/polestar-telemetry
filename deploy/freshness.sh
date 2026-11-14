#!/usr/bin/env bash
# Stale-data check, run every 15 minutes by polestar-freshness.timer. It asks the collector container when it last
# got data from the API. If that is longer ago than [alerts] stale_hours (default 6), or the container cannot be
# reached, it notifies once (see notify.sh) and pings $HEARTBEAT_URL/fail. While data is fresh it pings
# $HEARTBEAT_URL (a Healthchecks.io check URL), so an outside service also alerts when this whole machine goes
# quiet. Nothing from the database is sent. Before the first successful poll it does nothing.
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
. "$(dirname "$0")/notify.sh"
C="${COLLECTOR:-polestar-telemetry_collector_1}"
STATE="${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/polestar-telemetry}"
FLAG="$STATE/stale"
mkdir -p "$STATE"

ping() { [ -z "${HEARTBEAT_URL:-}" ] || curl -fsS -m 20 --retry 2 "${HEARTBEAT_URL%/}$1" >/dev/null 2>&1 || true; }

AGE="$(podman exec "$C" python -m polestar.freshness 2>/dev/null)" || AGE=down
LIMIT_H="$(podman exec "$C" python -c "from polestar import config; print(config.load()['alerts']['stale_hours'])" 2>/dev/null)" || LIMIT_H=6
[ "$AGE" != none ] || exit 0

stale=0; why=""
if [ "$AGE" = down ]; then
  stale=1; why="the collector container cannot be reached"
elif awk -v a="$AGE" -v h="$LIMIT_H" 'BEGIN { exit !(a > h * 3600) }'; then
  stale=1; why="no data from the Polestar API for $((AGE / 3600)) h (limit ${LIMIT_H} h)"
fi

if [ "$stale" = 1 ]; then
  ping /fail
  if [ ! -e "$FLAG" ]; then
    : > "$FLAG"
    notify "Polestar data is stale" "$why"
  fi
else
  ping ""
  if [ -e "$FLAG" ]; then
    rm -f "$FLAG"
    notify "Polestar data is flowing again" "the collector is polling normally"
  fi
fi
