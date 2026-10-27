#!/bin/bash
# DB Backup Script — executed on PostgreSQL first-start via initdb.d
# Creates a backup right after the cluster is initialized, and is safe to
# re-run manually (e.g. from a host cron) for the daily rotation.
#
# NOTE: This must NOT abort the initdb sequence. A non-zero exit here makes
# docker-entrypoint.sh abort and the container restart-loop with
# "dependency failed to start: container ... is unhealthy".
# That happened because the pg_backups volume is root-owned while postgres
# runs as uid 999 -> pg_dump redirect failed with "Permission denied".

set -uo pipefail

BACKUP_DIR="/var/lib/postgresql/backups"
RETENTION_DAYS=14

# The mounted volume may be root-owned. Try to take it over; if we cannot,
# fall back to a writable directory inside PGDATA so the backup still runs.
if ! mkdir -p "$BACKUP_DIR" 2>/dev/null || [ ! -w "$BACKUP_DIR" ]; then
    BACKUP_DIR="${PGDATA:-/var/lib/postgresql/data}/backups"
    mkdir -p "$BACKUP_DIR" 2>/dev/null || {
        echo "[BACKUP] WARNING: no writable backup directory found, skipping backup"
        exit 0
    }
    echo "[BACKUP] WARNING: /var/lib/postgresql/backups not writable, using $BACKUP_DIR"
fi

# Only create a new backup if today's doesn't exist yet
BACKUP_FILE="${BACKUP_DIR}/gbrain_$(date +%Y%m%d).sql.gz"
if [ -f "$BACKUP_FILE" ]; then
    echo "[BACKUP] Today's backup already exists: $BACKUP_FILE"
else
    echo "[BACKUP] Creating backup: $BACKUP_FILE"
    if pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-gbrain}" | gzip > "$BACKUP_FILE"; then
        echo "[BACKUP] Done: $(du -h "$BACKUP_FILE" | cut -f1)"
    else
        # Never let a failed dump take down the database init.
        rm -f "$BACKUP_FILE"
        echo "[BACKUP] WARNING: backup failed, continuing anyway"
        exit 0
    fi
fi

# Clean old backups
find "$BACKUP_DIR" -name 'gbrain_*.sql.gz' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

echo "[BACKUP] Daily backup check complete."
exit 0
