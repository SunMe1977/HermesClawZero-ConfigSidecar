#!/bin/bash
# Determine the directory where the script is located
cd "$(dirname "$0")"

echo "[START] System Services..."
python3 sync_watchdog.py &
docker compose down

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

echo "[OK] System running."
