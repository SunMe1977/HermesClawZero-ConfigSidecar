#!/bin/bash
# Determine the directory where the script is located
cd "$(dirname "$0")"

echo "[START] Docker Compose..."
docker compose down
docker compose up --build -d

echo "[OK] System running."
