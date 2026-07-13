#!/bin/bash
# DB Backup Script — executed on PostgreSQL first-start via initdb.d
# Creates a daily backup before the auto-update pipeline can rebuild the container.
# Backups land in /var/lib/postgresql/backups/ (persistent via pgdata volume).

set -euo pipefail

BACKUP_DIR="/var/lib/postgresql/backups"
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"

# Only create a new backup if today's doesn't exist yet
BACKUP_FILE="${BACKUP_DIR}/gbrain_$(date +%Y%m%d).sql.gz"
if [ -f "$BACKUP_FILE" ]; then
    echo "[BACKUP] Today's backup already exists: $BACKUP_FILE"
else
    echo "[BACKUP] Creating backup: $BACKUP_FILE"
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > "$BACKUP_FILE"
    echo "[BACKUP] Done: $(du -h "$BACKUP_FILE" | cut -f1)"
fi

# Clean old backups
find "$BACKUP_DIR" -name 'gbrain_*.sql.gz' -mtime +$RETENTION_DAYS -delete

echo "[BACKUP] Daily backup check complete."
