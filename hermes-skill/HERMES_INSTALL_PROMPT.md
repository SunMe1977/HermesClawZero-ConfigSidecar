# Hermes Agent — Install & Test HermesClawZero Skill

Copy this prompt into your Hermes agent conversation:

---

## Prompt for Hermes Agent

```
Install the HermesClawZero memory skill from the local repo and verify connectivity.

## Setup Steps

1. Copy the skill folder to your skills directory:
   Copy C:\dev\HermesClawZero-ConfigSidecar\hermes-skill to your Hermes skills path

2. Set these environment variables (User scope or session):
   - MEM_PUBLIC_URL = http://localhost:8010
   - API_KEY = MYSECRET!!1344
   - MEM_SYNC_DIR = C:\dev\HermesClawZero-ConfigSidecar\sync

3. Test that the API is alive:
   Request: GET http://localhost:8010/healthz
   Expected: {"status":"ok","database":"ok"}

4. Test capture a memory:
   Request: POST http://localhost:8010/capture
   Headers: x-api-key: MYSECRET!!1344
   Body: {"text": "Hermes agent installed memory skill successfully"}
   Expected: {"status":"ok", "page_id": <number>}

5. Test search:
   Request: GET http://localhost:8010/search?query=Hermes+memory+skill&key=MYSECRET!!1344
   Expected: array with the test memory

6. Test version endpoint:
   Request: GET http://localhost:8010/version
   Expected: {"status":"ok", "version": ...}

7. Verify dashboard reachable:
   http://localhost:8010/dashboard (Basic Auth: admin / HermesDash!2026)

## Docker Stack Status

The system runs as 3 Docker containers:
- hermesclawzero-configsidecar-api-1 (API :8010)
- gbrain-postgres (PostgreSQL :5666 with pgvector)
- gbrain-ollama (Ollama :11435 with nomic-embed-text)

All containers are healthy. Confirmation: run "docker ps" and verify all 3 are "Up" and "(healthy)".
```

## After installation

Once installed, the SKILL.md in hermes-skill will handle auto-memory behavior:
- Auto-load context on fresh chats
- Auto-save user preferences, facts, and project state
- Sync full chat history to the Hermes DB
