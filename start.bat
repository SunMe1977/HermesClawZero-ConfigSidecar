@echo off
cd /d C:\dev\HermesClawZero-ConfigSidecar

echo [START] Docker Compose neu starten...
docker compose down
docker compose up --build -d

echo [OK] System läuft.
