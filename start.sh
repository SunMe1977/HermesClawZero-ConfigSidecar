#!/bin/bash
# Determine the directory where the script is located
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# OSC 8 clickable link function (iTerm2, kitty, Terminal.app)
clickable() { printf "\e]8;;%s\a%s\e]8;;\a" "$1" "$2"; }

echo "[START] System Services..."

echo "[START] Cleaning old watchdog processes..."
pkill -f "python3 sync_watchdog.py" >/dev/null 2>&1 || true

docker compose down --remove-orphans

# Git pull — update host code before rebuild
echo "[START] Updating source code..."
git pull origin main

# Pre-rebuild backup
echo "[START] Pre-rebuild backup..."
python3 migrations/pre_rebuild_backup.py backup

# Docker cleanup (safe: keeps running containers + volumes)
echo "[START] Docker cleanup..."
docker system prune -a -f

PROVIDER=""
if [ -f .env ]; then
	PROVIDER="$(grep -E '^AI_PROVIDER=' .env | head -n 1 | cut -d'=' -f2 | tr -d '[:space:]')"
fi

if [ "$PROVIDER" = "ollama" ]; then
	echo "[START] AI_PROVIDER=ollama -> starting with Ollama profile"
	docker compose --profile ollama up --build -d
else
	echo "[START] AI_PROVIDER=${PROVIDER:-unset} -> starting without Ollama container"
	docker compose up --build -d
fi

echo "[START] Waiting for API health at http://localhost:8010/healthz ..."
for i in $(seq 1 30); do
	if curl -fsS "http://localhost:8010/healthz" >/dev/null 2>&1; then
		echo "[START] API is healthy."
		break
	fi
	sleep 1
done

echo "[START] Launching sync_watchdog.py in background"
python3 sync_watchdog.py &

echo ""
echo "============================================"
printf "  ${BOLD}Dashboard:${NC}  "
clickable "http://localhost:8010/dashboard" "http://localhost:8010/dashboard"
echo ""
printf "  ${BOLD}Health:${NC}     "
clickable "http://localhost:8010/healthz" "http://localhost:8010/healthz"
echo ""
echo "============================================"
echo "[OK] System running."
echo ""
