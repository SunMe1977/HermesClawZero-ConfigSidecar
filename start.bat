@echo off
cd /d "%~dp0"

echo [START] Cleaning old watchdog processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'sync_watchdog.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [START] System Services...
docker compose down

set "PROVIDER="
for /f "tokens=1,* delims==" %%A in (.env) do (
	if /I "%%A"=="AI_PROVIDER" set "PROVIDER=%%B"
)

if /I "%PROVIDER%"=="ollama" (
	echo [START] AI_PROVIDER=ollama ^> starting with Ollama profile
	docker compose --profile ollama up --build -d
) else (
	if "%PROVIDER%"=="" (
		echo [START] AI_PROVIDER is unset ^> starting without Ollama container
	) else (
		echo [START] AI_PROVIDER=%PROVIDER% ^> starting without Ollama container
	)
	docker compose up --build -d
)

echo [START] Waiting for API health at http://localhost:8010/healthz ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 30; $i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8010/healthz' -TimeoutSec 2; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 400){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if($ok){ Write-Host '[START] API is healthy.' } else { Write-Host '[START] API health check timed out; continuing anyway.' }"

echo [START] Launching sync_watchdog.py in background
start "" python sync_watchdog.py

echo [OK] System läuft.
