#!/bin/bash
echo "=== HermesClawZero-ConfigSidecar Setup ==="

# 1. Check Python & Docker
if ! command -v python3 &> /dev/null; then echo "[!] Python3 not found."; exit 1; fi
if ! command -v docker &> /dev/null; then echo "[!] Docker not found."; exit 1; fi

# 2. Dependencies
python3 -m pip install -r requirements.txt

# 3. Environment Config
touch .env
source .env 2>/dev/null

read -p "Enter OpenClaw API Key [$OPENCLAW_KEY]: " INPUT_OPENCLAW_KEY
read -p "Enter OpenAI API Key [$OPENAI_API_KEY]: " INPUT_OPENAI_KEY
read -p "Enter Gemini API Key [$GEMINI_API_KEY]: " INPUT_GEMINI_KEY
read -p "Enter Anthropic API Key [$ANTHROPIC_API_KEY]: " INPUT_ANTHROPIC_KEY
read -p "Enter OpenRouter API Key [$OPENROUTER_API_KEY]: " INPUT_OPENROUTER_KEY
read -p "Enter Database Password [$DB_PASSWORD]: " INPUT_DB_PASSWORD

cat <<EOF > .env
OPENCLAW_URL=https://openclawmemwin.postarmory.com
OPENCLAW_KEY=${INPUT_OPENCLAW_KEY:-$OPENCLAW_KEY}
OPENAI_API_KEY=${INPUT_OPENAI_KEY:-$OPENAI_API_KEY}
GEMINI_API_KEY=${INPUT_GEMINI_KEY:-$GEMINI_API_KEY}
ANTHROPIC_API_KEY=${INPUT_ANTHROPIC_KEY:-$ANTHROPIC_API_KEY}
OPENROUTER_API_KEY=${INPUT_OPENROUTER_KEY:-$OPENROUTER_API_KEY}
OPENCLAW_SYNC_DIR=$(pwd)/sync
DB_PASSWORD=${INPUT_DB_PASSWORD:-$DB_PASSWORD}
OLLAMA_HOST=http://host.docker.internal:11435
EOF

# 4. Ollama Setup
read -p "Run Ollama in Docker? (Y/n): " RUN_DOCKER
if [[ "$RUN_DOCKER" =~ ^[Yy]$ ]]; then
    docker compose up -d ollama
    sleep 10
    docker exec gbrain-ollama ollama pull nomic-embed-text
    docker exec gbrain-ollama ollama pull llama3.1
fi

# 5. Skills
SKILL_DIR="$HOME/.hermes/skills/productivity/hermesclawzero-memory"
mkdir -p "$SKILL_DIR"
cp -r hermes-skill/* "$SKILL_DIR/"

echo "=== Setup Complete ==="
