#!/bin/bash
# Determine the directory where the script is located
cd "$(dirname "$0")"

echo "[START] System Services..."

echo "[START] Cleaning old watchdog processes..."
pkill -f "python3 sync_watchdog.py" >/dev/null 2>&1 || true

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

echo "[OK] System running."
