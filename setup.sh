#!/bin/bash
echo "=== HermesClawZero-ConfigSidecar Setup ==="

# 1. Check Python & Docker
if ! command -v python3 &> /dev/null; then
    echo "[!] Python3 not found. Please install python3."
    exit 1
fi
if ! command -v docker &> /dev/null; then
    echo "[!] Docker not found. Please install Docker."
    exit 1
fi

# 2. Install Requirements
echo "[INFO] Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# 3. Setup .env
if [ -f .env ]; then
    echo "Loading existing configuration from .env..."
    export $(grep -v '^#' .env | xargs)
fi

# Set defaults
API_KEY=${OPENCLAW_KEY:-"YOUR_KEY_HERE"}
DB_PASSWORD=${DB_PASSWORD:-""}
MEM_PUBLIC_URL=${OPENCLAW_URL:-"https://openclawmemwin.postarmory.com"}
OLLAMA_HOST=${OLLAMA_HOST:-"http://host.docker.internal:11434"}

# Prompts
read -p "Enter API Key [$API_KEY]: " INPUT_API_KEY
API_KEY=${INPUT_API_KEY:-$API_KEY}

read -p "Enter Database Password [$DB_PASSWORD]: " INPUT_DB_PASSWORD
DB_PASSWORD=${INPUT_DB_PASSWORD:-$DB_PASSWORD}

read -p "Enter Memory API URL [$MEM_PUBLIC_URL]: " INPUT_MEM_PUBLIC_URL
MEM_PUBLIC_URL=${INPUT_MEM_PUBLIC_URL:-$MEM_PUBLIC_URL}

read -p "Enter Ollama URL [$OLLAMA_HOST]: " INPUT_OLLAMA_HOST
OLLAMA_HOST=${INPUT_OLLAMA_HOST:-$OLLAMA_HOST}

# Write .env
cat <<EOF > .env
OPENCLAW_URL=$MEM_PUBLIC_URL
OPENCLAW_KEY=$API_KEY
OPENCLAW_SYNC_DIR=$(pwd)/sync
DB_PASSWORD=$DB_PASSWORD
OLLAMA_HOST=$OLLAMA_HOST
EOF

echo ".env created successfully."

# 4. Copy Skills
echo "Copying Hermes skill..."
SKILL_DIR="$HOME/.hermes/skills/productivity/hermesclawzero-memory"
mkdir -p "$SKILL_DIR"
cp -r hermes-skill/* "$SKILL_DIR/"

echo "=== Setup Complete ==="
read -p "Do you want to run start.sh now? (Y/n): " RUN_START
if [[ "$RUN_START" =~ ^[Yy]$ ]]; then
    chmod +x start.sh
    ./start.sh
fi
