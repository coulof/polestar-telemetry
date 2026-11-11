#!/usr/bin/env bash
# Nightly backup of the telemetry database from the collector container, on the host.
# Uses SQLite's online backup (safe while the collector writes), gzips it and keeps the newest KEEP copies.
# The copies contain the VIN and positions: the folder is 700 and the files 600. Usage: backup.sh [DEST_DIR]
set -euo pipefail
C="${COLLECTOR:-polestar-telemetry_collector_1}"
DEST="${1:-${BACKUP_DIR:-$HOME/polestar-backups}}"
KEEP="${KEEP:-14}"

umask 077
mkdir -p "$DEST"
out="$DEST/telemetry-$(date +%Y%m%d-%H%M).db"
podman exec "$C" python -c "
import sqlite3
s = sqlite3.connect('/data/telemetry.db'); d = sqlite3.connect('/tmp/backup.db'); s.backup(d); d.close()"
podman cp "$C:/tmp/backup.db" "$out"
podman exec "$C" rm -f /tmp/backup.db
gzip -f "$out"
chmod 600 "$out.gz"        # podman cp keeps the container file mode, so set it explicitly
ls -1t "$DEST"/telemetry-*.db.gz | tail -n +"$((KEEP + 1))" | xargs -r rm --
echo "backup written: $out.gz ($(ls -1 "$DEST"/telemetry-*.db.gz | wc -l) kept)"
