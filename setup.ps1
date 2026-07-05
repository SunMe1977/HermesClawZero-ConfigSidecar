# === HermesClawZero-ConfigSidecar Setup ===
Write-Host '=== HermesClawZero-ConfigSidecar Setup ===' -ForegroundColor Cyan

# 1. Check Python & Docker
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) { Write-Host "[!] Python not detected." -ForegroundColor Red; exit }
if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) { Write-Host "[!] Docker not detected." -ForegroundColor Red; exit }

# 2. Dependencies
pip install -r requirements.txt

# 3. Environment Config
$envFile = '.env'
$config = @{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^([^=]+)=(.*)$') { $config[$matches[1].Trim()] = $matches[2].Trim() }
    }
}

function Get-Input { param([string]$key, [string]$prompt, [string]$default) $val = $config[$key]; if ([string]::IsNullOrWhiteSpace($val)) { $val = $default }; $input = Read-Host "$prompt [$val]"; if ([string]::IsNullOrWhiteSpace($input)) { return $val }; return $input }

$OPENCLAW_KEY = Get-Input "OPENCLAW_KEY" "Enter OpenClaw API Key" ""
$OPENAI_KEY = Get-Input "OPENAI_API_KEY" "Enter OpenAI API Key" ""
$GEMINI_KEY = Get-Input "GEMINI_API_KEY" "Enter Gemini API Key" ""
$ANTHROPIC_KEY = Get-Input "ANTHROPIC_API_KEY" "Enter Anthropic API Key" ""
$OPENROUTER_KEY = Get-Input "OPENROUTER_API_KEY" "Enter OpenRouter API Key" ""
$DB_PASSWORD = Get-Input "DB_PASSWORD" "Enter DB Password" ""

$content = @"
OPENCLAW_URL=https://openclawmemwin.postarmory.com
OPENCLAW_KEY=$OPENCLAW_KEY
OPENAI_API_KEY=$OPENAI_KEY
GEMINI_API_KEY=$GEMINI_KEY
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
OPENROUTER_API_KEY=$OPENROUTER_KEY
OPENCLAW_SYNC_DIR=$(Get-Location).Path\sync
DB_PASSWORD=$DB_PASSWORD
OLLAMA_HOST=http://host.docker.internal:11435
"@
$content | Out-File -FilePath $envFile -Encoding UTF8
Write-Host '.env saved.' -ForegroundColor Green

# 4. Ollama Setup
$runDocker = Read-Host "Run Ollama in Docker? (Y/N)"
if ($runDocker -eq 'Y') {
    docker compose up -d ollama
    Start-Sleep -Seconds 10
    docker exec gbrain-ollama ollama pull nomic-embed-text
    docker exec gbrain-ollama ollama pull llama3.1
}

# 5. Skills
$skillDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, 'hermes', 'skills', 'productivity', 'hermesclawzero-memory')
if (-not (Test-Path $skillDir)) { New-Item -ItemType Directory -Path $skillDir -Force }
Copy-Item -Path 'hermes-skill/*' -Destination $skillDir -Recurse -Force

Write-Host '=== Setup Complete ===' -ForegroundColor Green
