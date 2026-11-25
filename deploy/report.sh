#!/usr/bin/env bash
# Write the Claude reports on the host: stats and storage run inside the collector container, the Claude Code CLI
# (subscription login) runs here, because the container has no CLI.
# Usage: report.sh [PERIOD-ID]   (2026-W47, 2026-11, 2026-Q4 or 2026; default: every last full week, month,
#                                 quarter and year that has no report yet; FORCE=1 replaces an existing report)
# A daily timer runs it, so missed periods are caught up. Each failure is recorded (shown on the dashboard's
# Report tab) and notified, see notify.sh; the other periods still run, and the script exits non-zero at the end.
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
. "$(dirname "$0")/notify.sh"
C="${COLLECTOR:-polestar-telemetry_collector_1}"
CLAUDE="${CLAUDE_BIN:-claude}"
in_c() { podman exec "$C" python "$@"; }

fail() {   # fail ID WHY: record and notify, never data
  [ -z "$1" ] || printf '%s' "$2" | podman exec -i "$C" python -m polestar.report --failed "$1" >/dev/null 2>&1 || true
  notify "Polestar report failed" "${1:-reports}: $2"
}

one() {    # one ID: write and store the report for that period; returns non-zero on failure
  local id="$1" prompt out
  if [ "${FORCE:-0}" != 1 ] && in_c -m polestar.report --exists "$id"; then
    echo "report for $id already stored"; return 0
  fi
  prompt="$(in_c -m polestar.report --prompt "$id")" || { fail "$id" "failed while reading the stats"; return 1; }
  out="$(cd "$WORK" && printf '%s' "$prompt" | "$CLAUDE" -p --model "$MODEL" --tools "" --disable-slash-commands \
    --strict-mcp-config --no-session-persistence --output-format text)" \
    || { fail "$id" "failed while asking Claude (is the CLI logged in?)"; return 1; }   # neutral directory: no project context
  [ -n "$out" ] || { fail "$id" "failed while reading Claude's answer (it was empty)"; return 1; }
  printf '%s' "$out" | podman exec -i "$C" python -m polestar.report --store "$id" --model "$MODEL" \
    || { fail "$id" "failed while storing the report"; return 1; }
}

ENABLED="$(in_c -c "from polestar import config; print(config.load()['report']['enabled'])")" \
  || { fail "" "failed while reading the config (is the collector running?)"; exit 1; }
if [ "$ENABLED" != True ]; then
  echo "Claude reports disabled ([report] enabled = false in config.toml)"; exit 0
fi
MODEL="$(in_c -c "from polestar import config; print(config.load()['report']['model'])")" \
  || { fail "" "failed while reading the config"; exit 1; }
if [ -n "${1:-}" ]; then IDS="$1"
else IDS="$(in_c -m polestar.report --due)" || { fail "" "failed while finding the due periods"; exit 1; }
fi

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
status=0
for id in $IDS; do
  one "$id" || status=1
done
exit "$status"
