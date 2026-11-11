#!/usr/bin/env bash
# Generate the weekly "hot takes" report on the host: stats and storage run inside the collector
# container, the Claude Code CLI (subscription login) runs here, because the container has no CLI.
# Usage: weekly-report.sh [ISO-WEEK]     (default: last full week; FORCE=1 replaces an existing report)
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
C="${COLLECTOR:-polestar-telemetry_collector_1}"
CLAUDE="${CLAUDE_BIN:-claude}"

WEEK="${1:-$(podman exec "$C" python -m polestar.report --week-id)}"
if [ "${FORCE:-0}" != 1 ] && podman exec "$C" python -m polestar.report --exists "$WEEK"; then
  echo "report for $WEEK already stored"; exit 0
fi
MODEL="$(podman exec "$C" python -c "from polestar import config; print(config.load()['report']['model'])")"
PROMPT="$(podman exec "$C" python -m polestar.report --prompt "$WEEK")"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"   # neutral directory: the CLI loads no project context
REPORT="$(printf '%s' "$PROMPT" | "$CLAUDE" -p --model "$MODEL" --tools "" --disable-slash-commands \
  --strict-mcp-config --no-session-persistence --output-format text)"
[ -n "$REPORT" ] || { echo "claude returned an empty report" >&2; exit 1; }
printf '%s' "$REPORT" | podman exec -i "$C" python -m polestar.report --store "$WEEK" --model "$MODEL"
