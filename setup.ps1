# === HermesClawZero-ConfigSidecar Setup ===
Write-Host '=== HermesClawZero-ConfigSidecar Setup ===' -ForegroundColor Cyan

# 1. Check Dependencies
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) { Write-Host "[!] Python not detected." -ForegroundColor Red; exit }
if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) { Write-Host "[!] Docker not detected." -ForegroundColor Red; exit }

# 2. Environment Config
$envFile = '.env'
$config = @{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^([^=]+)=(.*)$') { $config[$matches[1].Trim()] = $matches[2].Trim() }
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

# 3. Provider Menu
Write-Host "Select Primary AI Provider:" -ForegroundColor Cyan
Write-Host "1. Local Ollama (Docker)"
Write-Host "2. OpenAI"
Write-Host "3. Google Gemini"
Write-Host "4. Anthropic"
Write-Host "5. OpenRouter"
$choice = Read-Host "Choice (1-5)"

$key = ""
$provider_key_name = ""
switch ($choice) {
    "1" { $provider = "ollama" }
    "2" { $provider = "openai"; $provider_key_name = "OPENAI_API_KEY" }
    "3" { $provider = "gemini"; $provider_key_name = "GEMINI_API_KEY" }
    "4" { $provider = "anthropic"; $provider_key_name = "ANTHROPIC_API_KEY" }
    "5" { $provider = "openrouter"; $provider_key_name = "OPENROUTER_API_KEY" }
}

if ($provider_key_name) {
    $key = Read-Host "Enter $provider_key_name"
}

# 4. Save Config
$syncDir = (Get-Location).Path + '\sync'
# 4. Save Config
$syncDir = (Get-Location).Path + '\sync'
$API_KEY = Get-Input "API_KEY" "Enter API Key" "YOUR_KEY_HERE"
$DASHBOARD_PASS = Get-Input "DASHBOARD_PASSWORD" "Enter Dashboard Password (for web login)" "admin"
$DB_PASSWORD = Get-Input "DB_PASSWORD" "Enter Database Password" ""
$TELEGRAM_TOKEN = Get-Input "TELEGRAM_BOT_TOKEN" "Enter Telegram Bot Token (optional)" ""
$TELEGRAM_CHAT_ID = Get-Input "TELEGRAM_CHAT_ID" "Enter Telegram Chat ID (optional)" ""
$APP_VERSION = Get-Input "APP_VERSION" "Enter Base App Version" "0.1.0"
$AUTO_UPDATE_ENABLED = Get-Input "AUTO_UPDATE_ENABLED" "Enable Auto Update Worker? (true/false)" "false"
$AUTO_UPDATE_APPLY = Get-Input "AUTO_UPDATE_APPLY" "Auto apply updates when found? (true/false)" "false"
$AUTO_UPDATE_INTERVAL_MINUTES = Get-Input "AUTO_UPDATE_INTERVAL_MINUTES" "Auto update check interval (minutes)" "60"
$AUTO_UPDATE_REMOTE = Get-Input "AUTO_UPDATE_REMOTE" "Git remote for updates" "origin"
$AUTO_UPDATE_BRANCH = Get-Input "AUTO_UPDATE_BRANCH" "Git branch for updates" "main"
$UPDATE_REPO_DIR = Get-Input "UPDATE_REPO_DIR" "Repo path used by updater" ((Get-Location).Path)
$UPDATE_RESTART_COMMAND = Get-Input "UPDATE_RESTART_COMMAND" "Restart command after update (optional)" ""

$content = @"
AI_PROVIDER=$provider
$provider_key_name=$key
API_URL=https://openclawmemwin.postarmory.com
API_KEY=$API_KEY
SYNC_DIR=$syncDir
DB_PASSWORD=$DB_PASSWORD
DASHBOARD_PASSWORD=$DASHBOARD_PASS
TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN
TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
APP_VERSION=$APP_VERSION
AUTO_UPDATE_ENABLED=$AUTO_UPDATE_ENABLED
AUTO_UPDATE_APPLY=$AUTO_UPDATE_APPLY
AUTO_UPDATE_INTERVAL_MINUTES=$AUTO_UPDATE_INTERVAL_MINUTES
AUTO_UPDATE_REMOTE=$AUTO_UPDATE_REMOTE
AUTO_UPDATE_BRANCH=$AUTO_UPDATE_BRANCH
UPDATE_REPO_DIR=$UPDATE_REPO_DIR
UPDATE_RESTART_COMMAND=$UPDATE_RESTART_COMMAND
OLLAMA_HOST=http://host.docker.internal:11435
"@
$content | Out-File -FilePath $envFile -Encoding UTF8
Write-Host '.env saved.' -ForegroundColor Green

# 5. Optional Ollama Setup
if ($provider -eq "ollama") {
    Write-Host "[INFO] Starting Ollama container..." -ForegroundColor Cyan
    docker compose up -d ollama
    Start-Sleep -Seconds 10
    docker exec gbrain-ollama ollama pull nomic-embed-text
    docker exec gbrain-ollama ollama pull llama3.1
}

# 6. Skills
$skillDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, 'hermes', 'skills', 'productivity', 'hermesclawzero-memory')
if (-not (Test-Path $skillDir)) { New-Item -ItemType Directory -Path $skillDir -Force }
Copy-Item -Path 'hermes-skill/*' -Destination $skillDir -Recurse -Force

Write-Host '=== Setup Complete ===' -ForegroundColor Green
