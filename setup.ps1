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

if (-not $provider) { Write-Host "[!] Invalid provider choice." -ForegroundColor Red; exit }

$OPENAI_API_KEY = if ($config.ContainsKey("OPENAI_API_KEY")) { $config["OPENAI_API_KEY"] } else { "" }
$GEMINI_API_KEY = if ($config.ContainsKey("GEMINI_API_KEY")) { $config["GEMINI_API_KEY"] } else { "" }
$ANTHROPIC_API_KEY = if ($config.ContainsKey("ANTHROPIC_API_KEY")) { $config["ANTHROPIC_API_KEY"] } else { "" }
$OPENROUTER_API_KEY = if ($config.ContainsKey("OPENROUTER_API_KEY")) { $config["OPENROUTER_API_KEY"] } else { "" }

if ($provider_key_name) {
    $currentProviderKey = if ($config.ContainsKey($provider_key_name)) { $config[$provider_key_name] } else { "" }
    $enteredProviderKey = Read-Host "Enter $provider_key_name [$currentProviderKey]"
    if ([string]::IsNullOrWhiteSpace($enteredProviderKey)) { $key = $currentProviderKey } else { $key = $enteredProviderKey }

    switch ($provider_key_name) {
        "OPENAI_API_KEY" { $OPENAI_API_KEY = $key }
        "GEMINI_API_KEY" { $GEMINI_API_KEY = $key }
        "ANTHROPIC_API_KEY" { $ANTHROPIC_API_KEY = $key }
        "OPENROUTER_API_KEY" { $OPENROUTER_API_KEY = $key }
    }
}

$defaultEmbeddingProvider = if ($config.ContainsKey("EMBEDDING_PROVIDER") -and -not [string]::IsNullOrWhiteSpace($config["EMBEDDING_PROVIDER"])) { $config["EMBEDDING_PROVIDER"] } else { "auto" }
if ($provider -eq "anthropic" -and $defaultEmbeddingProvider -eq "auto") { $defaultEmbeddingProvider = "openrouter" }
$inputEmbeddingProvider = Read-Host "Embedding provider (auto|ollama|openai|openrouter|gemini) [$defaultEmbeddingProvider]"
$EMBEDDING_PROVIDER = if ([string]::IsNullOrWhiteSpace($inputEmbeddingProvider)) { $defaultEmbeddingProvider } else { $inputEmbeddingProvider }
$EMBEDDING_PROVIDER = $EMBEDDING_PROVIDER.ToLowerInvariant()
if ($EMBEDDING_PROVIDER -notin @("auto", "ollama", "openai", "openrouter", "gemini")) {
    Write-Host "[SETUP] Invalid EMBEDDING_PROVIDER '$EMBEDDING_PROVIDER'. Falling back to 'auto'." -ForegroundColor Yellow
    $EMBEDDING_PROVIDER = "auto"
}

if ($EMBEDDING_PROVIDER -eq "openrouter" -and [string]::IsNullOrWhiteSpace($OPENROUTER_API_KEY)) {
    $OPENROUTER_API_KEY = Read-Host "Enter OPENROUTER_API_KEY for embeddings"
}
if ($EMBEDDING_PROVIDER -eq "openai" -and [string]::IsNullOrWhiteSpace($OPENAI_API_KEY)) {
    $OPENAI_API_KEY = Read-Host "Enter OPENAI_API_KEY for embeddings"
}
if ($EMBEDDING_PROVIDER -eq "gemini" -and [string]::IsNullOrWhiteSpace($GEMINI_API_KEY)) {
    $GEMINI_API_KEY = Read-Host "Enter GEMINI_API_KEY for embeddings"
}

# Auto-correct common key/provider mismatches to prevent runtime 401/500 loops.
if ($provider -eq "openai" -and -not [string]::IsNullOrWhiteSpace($OPENAI_API_KEY) -and $OPENAI_API_KEY.StartsWith("sk-or-")) {
    Write-Host "[SETUP] Detected OpenRouter key format in OPENAI_API_KEY. Switching AI_PROVIDER to openrouter." -ForegroundColor Yellow
    $provider = "openrouter"
    if ([string]::IsNullOrWhiteSpace($OPENROUTER_API_KEY)) { $OPENROUTER_API_KEY = $OPENAI_API_KEY }
    $OPENAI_API_KEY = ""
}

if ($provider -eq "openrouter" -and -not [string]::IsNullOrWhiteSpace($OPENROUTER_API_KEY) -and $OPENROUTER_API_KEY.StartsWith("sk-") -and -not $OPENROUTER_API_KEY.StartsWith("sk-or-")) {
    Write-Host "[SETUP] Detected OpenAI-style key in OPENROUTER_API_KEY. Switching AI_PROVIDER to openai." -ForegroundColor Yellow
    $provider = "openai"
    if ([string]::IsNullOrWhiteSpace($OPENAI_API_KEY)) { $OPENAI_API_KEY = $OPENROUTER_API_KEY }
    $OPENROUTER_API_KEY = ""
}

if ($EMBEDDING_PROVIDER -eq "openai" -and [string]::IsNullOrWhiteSpace($OPENAI_API_KEY) -and -not [string]::IsNullOrWhiteSpace($OPENROUTER_API_KEY)) {
    Write-Host "[SETUP] EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is empty. Using openrouter embeddings." -ForegroundColor Yellow
    $EMBEDDING_PROVIDER = "openrouter"
}

if ($EMBEDDING_PROVIDER -eq "openrouter" -and [string]::IsNullOrWhiteSpace($OPENROUTER_API_KEY) -and -not [string]::IsNullOrWhiteSpace($OPENAI_API_KEY)) {
    Write-Host "[SETUP] EMBEDDING_PROVIDER=openrouter but OPENROUTER_API_KEY is empty. Using openai embeddings." -ForegroundColor Yellow
    $EMBEDDING_PROVIDER = "openai"
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
$detectedRepoDir = (Get-Location).Path
$detectedRemote = "origin"
$detectedBranch = "main"
if (Get-Command "git" -ErrorAction SilentlyContinue) {
    $insideWorkTree = git rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -eq 0 -and ($insideWorkTree -join "").Trim() -eq "true") {
        $repoTop = git rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -eq 0 -and $repoTop) {
            $detectedRepoDir = ($repoTop -join "").Trim()
        }

        $upstream = git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null
        if ($LASTEXITCODE -eq 0 -and $upstream -and ($upstream -join "") -match '^([^/]+)/(.+)$') {
            $detectedRemote = $matches[1]
            $detectedBranch = $matches[2]
        }
        else {
            $currentBranch = git branch --show-current 2>$null
            if ($LASTEXITCODE -eq 0 -and $currentBranch) {
                $detectedBranch = ($currentBranch -join "").Trim()
            }
            $firstRemote = git remote 2>$null | Select-Object -First 1
            if ($firstRemote) {
                $detectedRemote = ($firstRemote -join "").Trim()
            }
        }
    }
}
$AUTO_UPDATE_REMOTE = Get-Input "AUTO_UPDATE_REMOTE" "Git remote for updates" $detectedRemote
$AUTO_UPDATE_BRANCH = Get-Input "AUTO_UPDATE_BRANCH" "Git branch for updates" $detectedBranch
$UPDATE_REPO_DIR = Get-Input "UPDATE_REPO_DIR" "Repo path used by updater" $detectedRepoDir
$UPDATE_RESTART_COMMAND = Get-Input "UPDATE_RESTART_COMMAND" "Restart command after update (optional)" ""

$detectedHermesDb = if ($config.ContainsKey("HERMES_DB_PATH")) { $config["HERMES_DB_PATH"] } else { "" }
if ([string]::IsNullOrWhiteSpace($detectedHermesDb)) {
    $candidatePaths = @(
        (Join-Path $env:USERPROFILE ".hermes\state.db"),
        (Join-Path $env:LOCALAPPDATA "hermes\state.db"),
        (Join-Path $env:USERPROFILE "hermes\state.db")
    )
    foreach ($p in $candidatePaths) {
        if (Test-Path $p) {
            $detectedHermesDb = $p
            break
        }
    }
}

if ([string]::IsNullOrWhiteSpace($detectedHermesDb)) {
    Write-Host "[SETUP] Searching for Hermes state.db (this can take a while)..." -ForegroundColor Yellow
    try {
        $found = Get-ChildItem -Path $env:USERPROFILE -Filter state.db -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'hermes' } |
            Select-Object -First 1
        if ($found) { $detectedHermesDb = $found.FullName }
    }
    catch {}
}

$HERMES_DB_PATH = Get-Input "HERMES_DB_PATH" "Hermes DB path for watchdog (optional)" $detectedHermesDb

$lines = @(
    "AI_PROVIDER=$provider"
    "EMBEDDING_PROVIDER=$EMBEDDING_PROVIDER"
)

if (-not [string]::IsNullOrWhiteSpace($OPENAI_API_KEY)) { $lines += "OPENAI_API_KEY=$OPENAI_API_KEY" }
if (-not [string]::IsNullOrWhiteSpace($GEMINI_API_KEY)) { $lines += "GEMINI_API_KEY=$GEMINI_API_KEY" }
if (-not [string]::IsNullOrWhiteSpace($ANTHROPIC_API_KEY)) { $lines += "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" }
if (-not [string]::IsNullOrWhiteSpace($OPENROUTER_API_KEY)) { $lines += "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" }

$lines += @(
    "API_KEY=$API_KEY"
    "SYNC_DIR=$syncDir"
    "DB_PASSWORD=$DB_PASSWORD"
    "DASHBOARD_PASSWORD=$DASHBOARD_PASS"
    "DASHBOARD_SESSION_SECRET=$( -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | ForEach-Object { [char]$_ }) )"
    "TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN"
    "TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID"
    "APP_VERSION=$APP_VERSION"
    "AUTO_UPDATE_ENABLED=$AUTO_UPDATE_ENABLED"
    "AUTO_UPDATE_APPLY=$AUTO_UPDATE_APPLY"
    "AUTO_UPDATE_INTERVAL_MINUTES=$AUTO_UPDATE_INTERVAL_MINUTES"
    "AUTO_UPDATE_REMOTE=$AUTO_UPDATE_REMOTE"
    "AUTO_UPDATE_BRANCH=$AUTO_UPDATE_BRANCH"
    "UPDATE_REPO_DIR=$UPDATE_REPO_DIR"
    "UPDATE_RESTART_COMMAND=$UPDATE_RESTART_COMMAND"
    "HERMES_DB_PATH=$HERMES_DB_PATH"
)
# Only ask for embedding model matching the chosen provider
$embedModels = @()
if ($provider -eq "openai" -or $EMBEDDING_PROVIDER -eq "openai") { $embedModels += "OPENAI_EMBED_MODEL=$(Get-Input 'OPENAI_EMBED_MODEL' 'OpenAI embedding model' 'text-embedding-3-small')" }
if ($provider -eq "openrouter" -or $EMBEDDING_PROVIDER -eq "openrouter") { $embedModels += "OPENROUTER_EMBED_MODEL=$(Get-Input 'OPENROUTER_EMBED_MODEL' 'OpenRouter embedding model' 'text-embedding-3-small')" }
if ($provider -eq "gemini" -or $EMBEDDING_PROVIDER -eq "gemini" -or $EMBEDDING_PROVIDER -eq "auto") { $embedModels += "GEMINI_EMBED_MODEL=$(Get-Input 'GEMINI_EMBED_MODEL' 'Gemini embedding model' 'models/text-embedding-004')" }
$lines += $embedModels
$lines += @(
    "OLLAMA_HOST=http://host.docker.internal:11435"
)

$content = $lines -join [Environment]::NewLine
$content | Out-File -FilePath $envFile -Encoding UTF8
Write-Host '.env saved.' -ForegroundColor Green

# 5. Optional Ollama Setup
if ($provider -eq "ollama") {
    Write-Host "[INFO] Starting Ollama container..." -ForegroundColor Cyan
    docker compose up -d ollama
    Start-Sleep -Seconds 10
    docker exec hc-sidecar-ollama ollama pull nomic-embed-text
    docker exec hc-sidecar-ollama ollama pull llama3.1
}

# 6. Skills
$skillDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, 'hermes', 'skills', 'productivity', 'hermesclawzero-memory')
if (-not (Test-Path $skillDir)) { New-Item -ItemType Directory -Path $skillDir -Force }
Copy-Item -Path 'hermes-skill/*' -Destination $skillDir -Recurse -Force

Write-Host '=== Setup Complete ===' -ForegroundColor Green
