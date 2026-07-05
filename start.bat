@echo off
cd /d C:\dev\HermesClawZero-ConfigSidecar

echo [START] System Services...
start "" python sync_watchdog.py
docker compose down
docker compose up --build -d

echo [OK] System läuft.
