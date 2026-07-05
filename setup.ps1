# === HermesClawZero-ConfigSidecar Setup ===
Write-Host '=== HermesClawZero-ConfigSidecar Setup ===' -ForegroundColor Cyan

# 1. Check Python & Docker
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "[!] Python not detected." -ForegroundColor Red; exit
}
if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
    Write-Host "[!] Docker not detected." -ForegroundColor Red; exit
}

# 2. Dependencies
Write-Host '[INFO] Installing Python dependencies...' -ForegroundColor Yellow
pip install -r requirements.txt

# 3. Environment Config
$envFile = '.env'
$config = @{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^([^=]+)=(.*)$') { $config[$matches[1].Trim()] = $matches[2].Trim() }
    }
}

$API_KEY = Read-Host "Enter API Key [$($config['OPENCLAW_KEY'])]"
if ([string]::IsNullOrWhiteSpace($API_KEY)) { $API_KEY = $config['OPENCLAW_KEY'] }

$content = @"
OPENCLAW_URL=https://openclawmemwin.postarmory.com
OPENCLAW_KEY=$API_KEY
OPENCLAW_SYNC_DIR=$(Get-Location).Path\sync
DB_PASSWORD=$($config['DB_PASSWORD'])
OLLAMA_HOST=http://host.docker.internal:11434
"@
$content | Out-File -FilePath $envFile -Encoding UTF8

# 4. Ollama Setup
$runDocker = Read-Host "Run Ollama in Docker? (Y/N)"
if ($runDocker -eq 'Y') {
    Write-Host "[INFO] Starting Ollama container..." -ForegroundColor Cyan
    docker compose up -d ollama
    Write-Host "[INFO] Waiting for Ollama to initialize (10s)..." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
    Write-Host "[INFO] Pulling models (this may take a minute)..." -ForegroundColor Green
    docker exec gbrain-ollama ollama pull nomic-embed-text
    docker exec gbrain-ollama ollama pull llama3.1
}

# 5. Skills
$skillDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, 'hermes', 'skills', 'productivity', 'hermesclawzero-memory')
if (-not (Test-Path $skillDir)) { New-Item -ItemType Directory -Path $skillDir -Force }
Copy-Item -Path 'hermes-skill/*' -Destination $skillDir -Recurse -Force

Write-Host '=== Setup Complete ===' -ForegroundColor Green
$runStart = Read-Host 'Run start.bat now? (Y/N)'
if ($runStart -eq 'Y') { Start-Process -FilePath 'start.bat' }
