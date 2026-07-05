#!/bin/bash
echo "=================================================="
echo "HermesClawZero Environment Setup"
echo "=================================================="

# 1. Check Python
if ! command -v python3 &> /dev/null; then
    echo "[!] Python3 not found. Please install python3 (e.g., sudo apt install python3 python3-pip)"
    exit 1
else
    echo "[OK] Python3 is installed."
fi

# 2. Check Docker
if ! command -v docker &> /dev/null; then
    echo "[!] Docker not found. Please install Docker."
    exit 1
else
    echo "[OK] Docker is installed."
fi

# 3. Install Requirements
echo "[INFO] Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# 4. Setup .env
if [ ! -f .env ]; then
    echo "[INFO] Generating .env from defaults..."
    echo "OPENCLAW_URL=https://openclawmemwin.postarmory.com" > .env
    echo "OPENCLAW_KEY=YOUR_KEY_HERE" >> .env
    echo "OPENCLAW_SYNC_DIR=./sync/" >> .env
    echo "[!] .env created. Please update your OPENCLAW_KEY!"
fi

echo ""
echo "=================================================="
echo "Setup Complete!"
echo "=================================================="
