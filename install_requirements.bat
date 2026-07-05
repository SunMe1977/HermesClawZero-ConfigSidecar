@echo off
echo ==================================================
echo HermesClawZero Environment Setup
echo ==================================================

:: 1. Check/Install Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] Python not detected. Attempting to install via winget...
    winget install Python.Python.3.11 --silent --accept-source-agreements
) else (
    echo [OK] Python is installed.
)

:: 2. Check Docker
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] Docker not detected. Please install Docker Desktop: https://www.docker.com/products/docker-desktop/
    echo Once installed, restart your terminal and run this script again.
) else (
    echo [OK] Docker is installed.
)

:: 3. Install Requirements
echo [INFO] Installing Python dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt

:: 4. Setup .env
if not exist .env (
    echo [INFO] Generating .env from defaults...
    echo OPENCLAW_URL=https://openclawmemwin.postarmory.com > .env
    echo OPENCLAW_KEY=YOUR_KEY_HERE >> .env
    echo OPENCLAW_SYNC_DIR=./sync/ >> .env
    echo [!] .env created. Please update your OPENCLAW_KEY!
)

echo.
echo ==================================================
echo Setup Complete!
echo ==================================================
pause
