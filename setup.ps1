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
$content = @"
AI_PROVIDER=$provider
$provider_key_name=$key
API_URL=https://openclawmemwin.postarmory.com
API_KEY=$($config['API_KEY'])
SYNC_DIR=$syncDir
DB_PASSWORD=$($config['DB_PASSWORD'])
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
