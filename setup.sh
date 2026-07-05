#!/bin/bash
echo "=== HermesClawZero-ConfigSidecar Setup ==="

# 1. Check Python & Docker
if ! command -v python3 &> /dev/null; then echo "[!] Python3 not found."; exit 1; fi
if ! command -v docker &> /dev/null; then echo "[!] Docker not found."; exit 1; fi

# 2. Dependencies
echo "[INFO] Installing dependencies..."
python3 -m pip install -r requirements.txt

# 3. Environment Config
touch .env
source .env 2>/dev/null

read -p "Enter API Key [$OPENCLAW_KEY]: " INPUT_API_KEY
OPENCLAW_KEY=${INPUT_API_KEY:-$OPENCLAW_KEY}

cat <<EOF > .env
OPENCLAW_URL=https://openclawmemwin.postarmory.com
OPENCLAW_KEY=$OPENCLAW_KEY
OPENCLAW_SYNC_DIR=$(pwd)/sync
DB_PASSWORD=${DB_PASSWORD:-""}
OLLAMA_HOST=http://host.docker.internal:11434
EOF

# 4. Ollama Setup
read -p "Run Ollama in Docker? (Y/n): " RUN_DOCKER
if [[ "$RUN_DOCKER" =~ ^[Yy]$ ]]; then
    echo "[INFO] Starting Ollama container..."
    docker compose up -d ollama
    echo "[INFO] Waiting 10s for initialization..."
    sleep 10
    echo "[INFO] Pulling models..."
    docker exec gbrain-ollama ollama pull nomic-embed-text
    docker exec gbrain-ollama ollama pull llama3.1
fi

# 5. Skills
echo "Copying Hermes skill..."
SKILL_DIR="$HOME/.hermes/skills/productivity/hermesclawzero-memory"
mkdir -p "$SKILL_DIR"
cp -r hermes-skill/* "$SKILL_DIR/"

echo "=== Setup Complete ==="
read -p "Run start.sh now? (Y/n): " RUN_START
if [[ "$RUN_START" =~ ^[Yy]$ ]]; then
    chmod +x start.sh
    ./start.sh
fi
