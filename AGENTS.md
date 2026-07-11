# HermesClawZero-ConfigSidecar — Agent Guide

Long-term memory sidecar for Hermes & OpenClaw. PostgreSQL + pgvector + Ollama embeddings. Capture, search, and review memories via CLI, API, MCP, or Dashboard.

## Quick Install (agent does it)

```bash
git clone https://github.com/SunMe1977/HermesClawZero-ConfigSidecar.git
cd HermesClawZero-ConfigSidecar
copy .env.example .env
docker compose --profile ollama up -d --build
```

After 15s: `http://localhost:8010/healthz` → `{"status":"ok"}`

## CLI (memory.py)

```bash
python memory.py capture "fact" [scope_id]
python memory.py search "query" [limit=5]
python memory.py autosave "text" [filename]
```

## MCP Server (6 tools)

```bash
pip install mcp requests
python mcp_server.py
```

Tools: capture_memory, search_memory, list_recent, memory_stats, delete_memory, review_memories

## API

- `POST /capture` — save memory (json: `{"text":"..."}`)
- `GET /search?query=...&limit=5` — search memories
- `GET /healthz` — health check
- Auth: `x-api-key` header or `?key=` param

## Env

| Key | Default | Description |
|-----|---------|-------------|
| `MEM_PUBLIC_URL` | `http://localhost:8010` | API base URL |
| `API_KEY` | — | Required for protected endpoints |
| `AI_PROVIDER` | `ollama` | Provider: ollama, openai, gemini, anthropic, openrouter |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama endpoint |

## Dashboard

`http://localhost:8010/dashboard` — memory timeline, search, tenant isolation, quick capture. Default login: `admin` / `HermesDash!2026`.

## Docker Stack

| Container | Port |
|-----------|------|
| `hermesclawzero-configsidecar-api-1` | `:8010` |
| `gbrain-postgres` | `:5666` |
| `gbrain-ollama` | `:11435` |
