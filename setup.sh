#!/bin/bash
echo "=== HermesClawZero-ConfigSidecar Setup ==="

# 1. Check Dependencies
if ! command -v python3 &> /dev/null; then echo "[!] Python3 not found."; exit 1; fi
if ! command -v docker &> /dev/null; then echo "[!] Docker not found."; exit 1; fi

# 2. Dependencies
python3 -m pip install -r requirements.txt

# 3. Provider Menu
echo "Select Primary AI Provider:"
echo "1. Local Ollama (Docker)"
echo "2. OpenAI"
echo "3. Google Gemini"
echo "4. Anthropic"
echo "5. OpenRouter"
read -p "Choice (1-5): " CHOICE

case $CHOICE in
    1) PROVIDER="ollama"; KEY_VAR="";;
    2) PROVIDER="openai"; KEY_VAR="OPENAI_API_KEY";;
    3) PROVIDER="gemini"; KEY_VAR="GEMINI_API_KEY";;
    4) PROVIDER="anthropic"; KEY_VAR="ANTHROPIC_API_KEY";;
    5) PROVIDER="openrouter"; KEY_VAR="OPENROUTER_API_KEY";;
    *) echo "Invalid choice"; exit 1;;
esac

KEY=""
if [ -n "$KEY_VAR" ]; then
    read -p "Enter $KEY_VAR: " KEY
fi

# Load current .env for defaults
if [ -f .env ]; then export $(grep -v '^#' .env | xargs); fi

read -p "Enter API Key [${API_KEY:-YOUR_KEY_HERE}]: " INPUT_API_KEY
read -p "Enter Dashboard Password [${DASHBOARD_PASSWORD:-admin}]: " INPUT_DASHBOARD_PASS
read -p "Enter Database Password [${DB_PASSWORD:-}]: " INPUT_DB_PASSWORD
read -p "Enter Base App Version [${APP_VERSION:-0.1.0}]: " INPUT_APP_VERSION
read -p "Enable Auto Update Worker? (true/false) [${AUTO_UPDATE_ENABLED:-false}]: " INPUT_AUTO_UPDATE_ENABLED
read -p "Auto apply updates when found? (true/false) [${AUTO_UPDATE_APPLY:-false}]: " INPUT_AUTO_UPDATE_APPLY
read -p "Auto update interval in minutes [${AUTO_UPDATE_INTERVAL_MINUTES:-60}]: " INPUT_AUTO_UPDATE_INTERVAL_MINUTES
read -p "Git remote for updates [${AUTO_UPDATE_REMOTE:-origin}]: " INPUT_AUTO_UPDATE_REMOTE
read -p "Git branch for updates [${AUTO_UPDATE_BRANCH:-main}]: " INPUT_AUTO_UPDATE_BRANCH
read -p "Repo path for updater [${UPDATE_REPO_DIR:-$(pwd)}]: " INPUT_UPDATE_REPO_DIR
read -p "Restart command after update (optional) [${UPDATE_RESTART_COMMAND:-}]: " INPUT_UPDATE_RESTART_COMMAND

# 4. Write .env
read -p "Enter Telegram Bot Token [${TELEGRAM_BOT_TOKEN:-}]: " INPUT_TELEGRAM_TOKEN
read -p "Enter Telegram Chat ID [${TELEGRAM_CHAT_ID:-}]: " INPUT_TELEGRAM_CHAT_ID

cat <<EOF > .env
AI_PROVIDER=$PROVIDER
$KEY_VAR=$KEY
API_URL=https://openclawmemwin.postarmory.com
API_KEY=${INPUT_API_KEY:-${API_KEY:-"YOUR_KEY_HERE"}}
SYNC_DIR=$(pwd)/sync
DB_PASSWORD=${INPUT_DB_PASSWORD:-$DB_PASSWORD}
DASHBOARD_PASSWORD=${INPUT_DASHBOARD_PASS:-${DASHBOARD_PASSWORD:-admin}}
TELEGRAM_BOT_TOKEN=${INPUT_TELEGRAM_TOKEN:-$TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${INPUT_TELEGRAM_CHAT_ID:-$TELEGRAM_CHAT_ID}
APP_VERSION=${INPUT_APP_VERSION:-${APP_VERSION:-0.1.0}}
AUTO_UPDATE_ENABLED=${INPUT_AUTO_UPDATE_ENABLED:-${AUTO_UPDATE_ENABLED:-false}}
AUTO_UPDATE_APPLY=${INPUT_AUTO_UPDATE_APPLY:-${AUTO_UPDATE_APPLY:-false}}
AUTO_UPDATE_INTERVAL_MINUTES=${INPUT_AUTO_UPDATE_INTERVAL_MINUTES:-${AUTO_UPDATE_INTERVAL_MINUTES:-60}}
AUTO_UPDATE_REMOTE=${INPUT_AUTO_UPDATE_REMOTE:-${AUTO_UPDATE_REMOTE:-origin}}
AUTO_UPDATE_BRANCH=${INPUT_AUTO_UPDATE_BRANCH:-${AUTO_UPDATE_BRANCH:-main}}
UPDATE_REPO_DIR=${INPUT_UPDATE_REPO_DIR:-${UPDATE_REPO_DIR:-$(pwd)}}
UPDATE_RESTART_COMMAND=${INPUT_UPDATE_RESTART_COMMAND:-$UPDATE_RESTART_COMMAND}
OLLAMA_HOST=http://host.docker.internal:11435
EOF
echo ".env saved."

# 5. Ollama Setup
if [ "$PROVIDER" == "ollama" ]; then
    echo "[INFO] Starting Ollama container..."
    docker compose up -d ollama
    sleep 10
    docker exec gbrain-ollama ollama pull nomic-embed-text
    docker exec gbrain-ollama ollama pull llama3.1
fi

# 6. Skills
SKILL_DIR="$HOME/.hermes/skills/productivity/hermesclawzero-memory"
mkdir -p "$SKILL_DIR"
cp -r hermes-skill/* "$SKILL_DIR/"

echo "=== Setup Complete ==="
read -p "Run start.sh now? (Y/n): " RUN_START
if [[ "$RUN_START" =~ ^[Yy]$ ]]; then
    chmod +x start.sh
    ./start.sh
fi
