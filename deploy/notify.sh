# Sourced by the deploy scripts. notify TITLE MESSAGE: always prints to stderr (so it lands in the journal) and,
# when NOTIFY_URL is set, POSTs the message there (ntfy-style: the title goes in a header). The message holds
# only the step and exit code, never data from the database. Set the URL for the timers with
#   systemctl --user edit polestar-report     ->  [Service]  Environment=NOTIFY_URL=https://ntfy.sh/<your-secret-topic>
notify() {
  echo "$1: $2" >&2
  [ -z "${NOTIFY_URL:-}" ] || curl -fsS -m 20 -H "Title: $1" -d "$2" "$NOTIFY_URL" >/dev/null 2>&1 || true
}
