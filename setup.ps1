# === HermesClawZero-ConfigSidecar Setup ===
Write-Host '=== HermesClawZero-ConfigSidecar Setup ===' -ForegroundColor Cyan

# 1. Check/Install Python & Docker
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "[!] Python not detected. Please install Python 3.11+." -ForegroundColor Red
    exit
}
if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
    Write-Host "[!] Docker not detected. Please install Docker Desktop." -ForegroundColor Red
    exit
}

# 2. Install Requirements
Write-Host '[INFO] Installing Python dependencies...' -ForegroundColor Yellow
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Environment Config
$envFile = '.env'
$config = @{}

if (Test-Path $envFile) {
    Write-Host 'Loading existing configuration from .env...'
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^([^=]+)=(.*)$') {
            $k = $matches[1].Trim()
            $v = $matches[2].Trim()
            if ($v -ne "True") { $config[$k] = $v }
        }
    }
}

function Get-Input {
    param([string]$key, [string]$prompt, [string]$default)
    $val = $config[$key]
    if ([string]::IsNullOrWhiteSpace($val)) { $val = $default }
    
    $input = Read-Host "$prompt [$val]"
    if ([string]::IsNullOrWhiteSpace($input)) { return $val }
    return $input
}

function Get-ValidatedUrl {
    param([string]$key, [string]$prompt, [string]$default)
    $val = Get-Input -key $key -prompt $prompt -default $default
    if (-not $val.StartsWith("http")) {
        $val = "http://" + $val
    }
    return $val
}

$API_KEY = Get-Input -key "OPENCLAW_KEY" -prompt "Enter HermesClawZero-ConfigSidecar API Key" -default "YOUR_KEY_HERE"
$DB_PASSWORD = Get-Input -key "DB_PASSWORD" -prompt "Enter Database Password" -default ""
$MEM_PUBLIC_URL = Get-ValidatedUrl -key "OPENCLAW_URL" -prompt "Enter Memory API URL" -default "https://openclawmemwin.postarmory.com"
$OLLAMA_HOST = Get-ValidatedUrl -key "OLLAMA_HOST" -prompt "Enter Ollama URL" -default "http://host.docker.internal:11434"

$syncDir = (Get-Location).Path + '\sync'
$content = @"
OPENCLAW_URL=$MEM_PUBLIC_URL
OPENCLAW_KEY=$API_KEY
OPENCLAW_SYNC_DIR=$syncDir
DB_PASSWORD=$DB_PASSWORD
OLLAMA_HOST=$OLLAMA_HOST
"@

$content | Out-File -FilePath $envFile -Encoding UTF8
Write-Host '.env saved.' -ForegroundColor Green

Write-Host 'Copying Hermes skill...'
$skillDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, 'hermes', 'skills', 'productivity', 'hermesclawzero-memory')
if (-not (Test-Path $skillDir)) { New-Item -ItemType Directory -Path $skillDir -Force }
Copy-Item -Path 'hermes-skill/*' -Destination $skillDir -Recurse -Force

Write-Host '=== Setup Complete ===' -ForegroundColor Green
$runStart = Read-Host 'Do you want to run start.bat now? (Y/N)'
if ($runStart -eq 'Y') {
    Start-Process -FilePath 'start.bat' -Wait
}
