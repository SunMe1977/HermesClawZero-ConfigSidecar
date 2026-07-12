# HermesClawZero-ConfigSidecar — Agent Guide

Long-term memory sidecar for Hermes/OpenClaw. PostgreSQL + pgvector + Ollama embeddings. Dashboard, CLI, MCP (20 tools), REST API. Bi-temporal versioning, knowledge graph, episodic memory, auto-import.

## Quick Install
```bash
git clone https://github.com/SunMe1977/HermesClawZero-ConfigSidecar.git && cd $_
cp .env.example .env && docker compose --profile ollama up -d --build
```
Verify: `curl http://localhost:8010/healthz` → `{"status":"ok"}`

## CLI (`memory.py`)
```
capture "fact" [scope] | search "query" [limit=5] | autosave "text" [filename]
```

## API (key via `x-api-key` header or `?key=`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /capture | Store memory |
| GET | /search?query=&days_back= | Hybrid vector+lexical search |
| GET | /ask?q= | Q&A via vector+graph+LLM |
| GET | /graph/rag?q= | Graph-augmented retrieval |
| GET | /why/{id} | Bi-temporal version history |
| GET | /timeline | Change timeline |
| GET | /episodic/timeline | Event/milestone timeline |
| POST | /optimizer/* | Decay, dedup, reflect, tiers |
| GET | /nudge | Top memories for context |

## MCP (20 tools, auto-discover via `mcp.json`)
```
hermes mcp add hermesclawzero --command "python mcp_server.py"
```
Tools: capture, search, ask, graph entities/traverse/rag, why, timeline, episodic, nudge, dedup, tiers, reflection, optimize, feedback, merge, update, dashboard_stats.

## Dashboard
`http://localhost:8010/dashboard` — Memory Galaxy visualization, search/filter, optimizer controls, health review, export.

## Env
`MEM_PUBLIC_URL` (default :8010) · `API_KEY` (required) · `AI_PROVIDER` (ollama) · `OLLAMA_HOST`
