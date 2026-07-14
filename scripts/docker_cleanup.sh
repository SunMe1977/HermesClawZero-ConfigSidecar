#!/bin/bash
# Docker Cleanup — safe automated prune
# Runs: docker system prune -a -f (removes unused images, containers, build cache)
# Safe because: keeps running containers, keeps volumes with data
# Run weekly: cron or systemd timer
# Logs to: /var/log/docker_cleanup.log

set -euo pipefail

LOG="/var/log/docker_cleanup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] Starting docker cleanup..." >> "$LOG"

BEFORE=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "unknown")

docker system prune -a -f >> "$LOG" 2>&1

AFTER=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "unknown")

echo "[$TIMESTAMP] Done. Before: $BEFORE | After: $AFTER" >> "$LOG"
echo "----------------------------------------" >> "$LOG"
