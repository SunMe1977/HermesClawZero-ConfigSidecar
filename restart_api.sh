#!/bin/bash
cd "$(dirname "$0")"
echo "[UPDATE] Pulling latest code..."
git pull origin main
echo "[UPDATE] Rebuilding API containers..."
docker compose build api1 api2
echo "[UPDATE] Restarting API containers (no-deps preserves DB/redis/pgbouncer)..."
docker compose up -d --no-deps --force-recreate api1 api2 caddy
echo "[UPDATE] Done."
