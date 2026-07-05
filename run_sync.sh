#!/bin/bash
# Load OPENCLAW_URL / OPENCLAW_KEY from .env
set -a
[ -f .env ] && source .env
set +a
python memory_sync.py
