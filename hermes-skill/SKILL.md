---
name: hermesclawzero-memory
description: Long-term semantic memory via HermesClawZero Sidecar — auto-captures facts, preferences, and decisions during conversation.
---

# HermesClawZero Memory Manager

Persistent, searchable long-term memory via HermesClawZero Sidecar API (PostgreSQL + pgvector + Ollama embeddings).

## 🧠 Auto-Capture (Do This During Every Conversation)

**Whenever the user provides important information, immediately capture it:**
```
python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<summary of fact / preference / decision>"
```

### What to auto-capture:
| Trigger | Example |
|---------|---------|
| User states a preference | `capture "User prefers concise responses in German"` |
| User gives an instruction | `capture "User wants auto-capture enabled for all chats"` |
| Project decision made | `capture "Switched web search backend from Firecrawl to DuckDuckGo (ddgs)"` |
| Environment info learned | `capture "HermesClawZero running on Windows, Docker stack on ports 8010/5666/11435"` |
| Key file path established | `capture "Repo path: C:\\dev\\HermesClawZero-ConfigSidecar"` |
| User confirms something works/doesn't | `capture "User confirmed Docker stack healthy, capture+search functional"` |

**Do NOT capture:**
- Secrets, passwords, API keys (the .env is never shared)
- Every single trivial message
- Content that's already in the current session context

## 📋 Commands

### capture
Save a fact/memory to global chat (always findable via default search):
```
python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<text>" [scope_id]
```
Do **not** pass a `chat_id` — memories go to `chat_id=global` and are always searchable.

### search
Retrieve relevant memories:
```
python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "<query>" [limit=5]
```

### autosave
Write longer content as a single capture entry:
```
python C:\dev\HermesClawZero-ConfigSidecar\memory.py autosave "<text>" [filename]
```

## 🚀 Auto-Load on Fresh Chat

When a new session starts (and no prior context exists), load recent context:
```
python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "user profile preferences current project state" 5
```

## 📁 Files & Architecture

| File | Purpose |
|------|---------|
| `memory.py` | CLI tool: capture / search / autosave (reads .env from repo root) |
| `memory_sync.py` | Watchdog daemon — watches `sync/` and `inbox/` folders, auto-imports files to API |
| `main.py` | FastAPI server (port 8010) |
| `docker-compose.yml` | Orchestrates API + PostgreSQL/pgvector + Ollama |
| `.env` | Config: API_KEY, AI_PROVIDER=ollama, DB_PASSWORD, OLLAMA_HOST |

## 🐳 Docker Stack

After `docker compose --profile ollama up -d --build`:
| Container | Port | Role |
|-----------|------|------|
| `hermesclawzero-configsidecar-api-1` | `:8010` | FastAPI + capture/search endpoints |
| `gbrain-postgres` | `:5666` | PostgreSQL 16 + pgvector extension |
| `gbrain-ollama` | `:11435` | Ollama with nomic-embed-text for embeddings |

Health check: `curl http://localhost:8010/healthz`
Dashboard: `http://localhost:8010/dashboard` (Basic Auth)
