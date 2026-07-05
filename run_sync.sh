#!/bin/bash
# Load API_URL / API_KEY from .env
set -a
[ -f .env ] && source .env
set +a
python memory_sync.py
