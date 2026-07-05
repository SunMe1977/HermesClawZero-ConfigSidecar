#!/bin/bash

if [ -f .env ]; then
	set -a
	. ./.env
	set +a
fi

export MEM_PUBLIC_URL=${MEM_PUBLIC_URL:-${OPENCLAW_URL:-http://localhost:8000}}
export API_KEY=${API_KEY:-${OPENCLAW_KEY:-""}}

if [ -z "$API_KEY" ]; then
	echo "ERROR: API_KEY is not set. Set API_KEY (or OPENCLAW_KEY) in .env."
	exit 1
fi

python memory_sync.py
