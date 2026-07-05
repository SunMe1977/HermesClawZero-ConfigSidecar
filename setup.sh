#!/bin/bash
echo "=== HermesClawZero-ConfigSidecar Setup ==="

# Load existing .env if it exists
if [ -f .env ]; then
    echo "Loading existing configuration from .env..."
    # Export variables from .env to current shell
    export $(grep -v '^#' .env | xargs)
fi

# Set defaults from existing or hardcoded
API_KEY=${API_KEY:-""}
DB_PASSWORD=${DB_PASSWORD:-""}
MEM_PUBLIC_URL=${MEM_PUBLIC_URL:-http://localhost:8000}
OLLAMA_HOST=${OLLAMA_HOST:-http://host.docker.internal:11434}

# Prompts
read -p "Enter HermesClawZero-ConfigSidecar API Key [$API_KEY]: " INPUT_API_KEY
API_KEY=${INPUT_API_KEY:-$API_KEY}

read -p "Enter Database Password [$DB_PASSWORD]: " INPUT_DB_PASSWORD
DB_PASSWORD=${INPUT_DB_PASSWORD:-$DB_PASSWORD}

read -p "Enter Memory API URL [$MEM_PUBLIC_URL]: " INPUT_MEM_PUBLIC_URL
MEM_PUBLIC_URL=${INPUT_MEM_PUBLIC_URL:-$MEM_PUBLIC_URL}

read -p "Enter Ollama URL [$OLLAMA_HOST]: " INPUT_OLLAMA_HOST
OLLAMA_HOST=${INPUT_OLLAMA_HOST:-$OLLAMA_HOST}

# Write .env
cat <<EOF > .env
API_KEY=$API_KEY
DB_PASSWORD=$DB_PASSWORD
MEM_PUBLIC_URL=$MEM_PUBLIC_URL
OLLAMA_HOST=$OLLAMA_HOST
MEM_SYNC_DIR=$(pwd)/sync
EOF

echo ".env created successfully."

echo "Copying Hermes skill..."
SKILL_DIR="$HOME/.hermes/skills/productivity/hermesclawzero-memory"
mkdir -p "$HOME/.hermes/skills/productivity"
cp -r hermes-skill/* "$SKILL_DIR/"

echo "=== Setup Complete ==="
read -p "Do you want to run start.sh now? (Y/n): " RUN_START
if [[ "$RUN_START" =~ ^[Yy]$ ]]; then
    chmod +x start.sh
    ./start.sh
fi
