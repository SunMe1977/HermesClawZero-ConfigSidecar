#!/bin/bash
# Determine the directory where the script is located
cd "$(dirname "$0")"

echo "[START] System Services..."
python3 sync_watchdog.py &
docker compose down
docker compose up --build -d

echo "[OK] System running."
