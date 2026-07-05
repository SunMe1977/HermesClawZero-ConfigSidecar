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

read -p "Enter OpenClaw API Key [${OPENCLAW_KEY:-YOUR_KEY_HERE}]: " INPUT_OPENCLAW_KEY
read -p "Enter Dashboard Password [${DASHBOARD_PASSWORD:-admin}]: " INPUT_DASHBOARD_PASS
read -p "Enter Database Password [${DB_PASSWORD:-}]: " INPUT_DB_PASSWORD

# 4. Write .env
cat <<EOF > .env
AI_PROVIDER=$PROVIDER
$KEY_VAR=$KEY
OPENCLAW_URL=https://openclawmemwin.postarmory.com
OPENCLAW_KEY=${INPUT_OPENCLAW_KEY:-${OPENCLAW_KEY:-"YOUR_KEY_HERE"}}
OPENCLAW_SYNC_DIR=$(pwd)/sync
DB_PASSWORD=${INPUT_DB_PASSWORD:-$DB_PASSWORD}
DASHBOARD_PASSWORD=${INPUT_DASHBOARD_PASS:-${DASHBOARD_PASSWORD:-admin}}
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
