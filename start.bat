@echo off
cd /d "%~dp0"

echo [START] Cleaning old watchdog processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'sync_watchdog.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

set "PROVIDER="
set "ENV_API_KEY="
set "ENV_DASHBOARD_PASSWORD="
set "ENV_DASHBOARD_SESSION_SECRET="
for /f "tokens=1,* delims==" %%A in (.env) do (
	if /I "%%A"=="AI_PROVIDER" set "PROVIDER=%%B"
	if /I "%%A"=="API_KEY" set "ENV_API_KEY=%%B"
	if /I "%%A"=="DASHBOARD_PASSWORD" set "ENV_DASHBOARD_PASSWORD=%%B"
	if /I "%%A"=="DASHBOARD_SESSION_SECRET" set "ENV_DASHBOARD_SESSION_SECRET=%%B"
)

if "%ENV_DASHBOARD_PASSWORD%"=="" (
	echo [ERROR] DASHBOARD_PASSWORD is missing in .env
	exit /b 1
)
if /I "%ENV_DASHBOARD_PASSWORD%"=="admin" (
	echo [ERROR] DASHBOARD_PASSWORD must not be 'admin'. Update .env and retry.
	exit /b 1
)
if "%ENV_DASHBOARD_SESSION_SECRET%"=="" (
	echo [ERROR] DASHBOARD_SESSION_SECRET is missing in .env
	exit /b 1
)
if "%ENV_DASHBOARD_SESSION_SECRET%"=="%ENV_API_KEY%" (
	echo [ERROR] DASHBOARD_SESSION_SECRET must not equal API_KEY. Update .env and retry.
	exit /b 1
)

echo [START] System Services...
docker compose down --remove-orphans

rem Auto-detect pgdata volume PG version for build arg
set "PG_BUILD_ARG="
docker volume inspect hermesclawzero-configsidecar_pgdata >nul 2>&1
if not errorlevel 1 (
    echo [START] Existing pgdata volume found — checking PG version...
    docker run --rm -v hermesclawzero-configsidecar_pgdata:/data alpine cat /data/PG_VERSION 2>nul | findstr /c:"15" >nul 2>&1
    if not errorlevel 1 (
        set "PG_BUILD_ARG=--build-arg PGVECTOR_IMAGE=pgvector/pgvector:0.7.4-pg15"
        echo [START] Detected PG15 volume — using pgvector/pgvector:0.7.4-pg15
    ) else (
        echo [START] Detected PG17+ volume — using default image
    )
) else (
    echo [START] No existing pgdata volume — fresh install, using default image
)

if /I "%PROVIDER%"=="ollama" (
    echo [START] AI_PROVIDER=ollama ^> starting with Ollama profile
    docker compose %PG_BUILD_ARG% --profile ollama up --build -d
) else (
    if "%PROVIDER%"=="" (
        echo [START] AI_PROVIDER is unset ^> starting without Ollama container
    ) else (
        echo [START] AI_PROVIDER=%PROVIDER% ^> starting without Ollama container
    )
    docker compose %PG_BUILD_ARG% up --build -d
)

echo [START] Waiting for API health at http://localhost:8010/healthz ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 30; $i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8010/healthz' -TimeoutSec 2; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 400){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 3 }; if($ok){ Write-Host '[START] API is healthy.'; exit 0 } else { Write-Host '[ERROR] API health check timed out. Showing API logs:'; exit 1 }"
if errorlevel 1 (
    docker compose logs caddy --tail=30
    docker compose logs api1 --tail=20
    exit /b 1
)

echo [START] Launching sync_watchdog.py in background
start "" python sync_watchdog.py

echo [OK] System running.
