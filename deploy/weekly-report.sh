#!/usr/bin/env bash
# Generate the weekly "hot takes" report on the host: stats and storage run inside the collector
# container, the Claude Code CLI (subscription login) runs here, because the container has no CLI.
# Usage: weekly-report.sh [ISO-WEEK]     (default: last full week; FORCE=1 replaces an existing report)
# On failure it records the reason (shown on the dashboard's Report tab) and notifies, see notify.sh.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
. "$(dirname "$0")/notify.sh"
C="${COLLECTOR:-polestar-telemetry_collector_1}"
CLAUDE="${CLAUDE_BIN:-claude}"

WEEK="${1:-}"
STEP="finding the week"
on_error() {
  local code=$?
  trap - ERR
  local why="failed while $STEP (exit $code)"
  [ -z "$WEEK" ] || printf '%s' "$why" | podman exec -i "$C" python -m polestar.report --failed "$WEEK" >/dev/null 2>&1 || true
  notify "Polestar weekly report failed" "${WEEK:-last week}: $why"
  exit "$code"
}
trap on_error ERR

[ -n "$WEEK" ] || WEEK="$(podman exec "$C" python -m polestar.report --week-id)"
STEP="reading the stats"
if [ "${FORCE:-0}" != 1 ] && podman exec "$C" python -m polestar.report --exists "$WEEK"; then
  echo "report for $WEEK already stored"; exit 0
fi
MODEL="$(podman exec "$C" python -c "from polestar import config; print(config.load()['report']['model'])")"
PROMPT="$(podman exec "$C" python -m polestar.report --prompt "$WEEK")"
STEP="asking Claude (is the CLI logged in?)"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"   # neutral directory: the CLI loads no project context
REPORT="$(printf '%s' "$PROMPT" | "$CLAUDE" -p --model "$MODEL" --tools "" --disable-slash-commands \
  --strict-mcp-config --no-session-persistence --output-format text)"
STEP="reading Claude's answer (it was empty)"
[ -n "$REPORT" ] || false
STEP="storing the report"
printf '%s' "$REPORT" | podman exec -i "$C" python -m polestar.report --store "$WEEK" --model "$MODEL"
