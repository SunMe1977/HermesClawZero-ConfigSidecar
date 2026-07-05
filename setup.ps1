# === HermesClawZero-ConfigSidecar Setup ===
$envFile = '.env'
$config = @{}

if (Test-Path $envFile) {
    Write-Host 'Loading existing configuration from .env...'
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^([^=]+)=(.*)$') {
            $k = $matches[1].Trim()
            $v = $matches[2].Trim()
            # Filter out corrupted "True" values
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
    
    # Ensure it starts with http
    if (-not $val.StartsWith("http")) {
        Write-Host "Warning: '$val' does not start with http. Adding http:// prefix." -ForegroundColor Yellow
        $val = "http://" + $val
    }
    return $val
}

$API_KEY = Get-Input -key "API_KEY" -prompt "Enter HermesClawZero-ConfigSidecar API Key" -default ""
$DB_PASSWORD = Get-Input -key "DB_PASSWORD" -prompt "Enter Database Password" -default ""
$MEM_PUBLIC_URL = Get-ValidatedUrl -key "MEM_PUBLIC_URL" -prompt "Enter Memory API URL" -default "http://localhost:8000/"
$OLLAMA_HOST = Get-ValidatedUrl -key "OLLAMA_HOST" -prompt "Enter Ollama URL" -default "http://host.docker.internal:11434"

$syncDir = (Get-Location).Path + '\sync'
$content = @"
API_KEY=$API_KEY
DB_PASSWORD=$DB_PASSWORD
MEM_PUBLIC_URL=$MEM_PUBLIC_URL
OLLAMA_HOST=$OLLAMA_HOST
MEM_SYNC_DIR=$syncDir
"@

$content | Out-File -FilePath $envFile -Encoding UTF8
Write-Host '.env saved.'

Write-Host 'Copying Hermes skill...'
$skillDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, 'hermes', 'skills', 'productivity', 'hermesclawzero-memory')
if (-not (Test-Path $skillDir)) { New-Item -ItemType Directory -Path $skillDir -Force }
Copy-Item -Path 'hermes-skill/*' -Destination $skillDir -Recurse -Force

Write-Host '=== Setup Complete ==='
$runStart = Read-Host 'Do you want to run start.bat now? (Y/N)'
if ($runStart -eq 'Y') {
    Start-Process -FilePath 'start.bat' -Wait
}
