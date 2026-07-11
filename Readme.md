![Logo](images/logo.webp "HermesClaw Zero-Config Sidecar")

![License](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Docker](https://img.shields.io/badge/docker-required-2496ED.svg)

# HermesClaw Zero-Config Sidecar

**PostgreSQL‑backed long‑term memory for Hermes & OpenClaw — with a live Dashboard.**  
Spin up Docker, open `http://localhost:8010/dashboard`, and your agent instantly remembers across sessions.  
No API keys, no cloud, no config — just `docker compose up`.

---

## 🚀 10‑Second Demo

<img src="images/demo-terminal.svg" alt="Terminal demo" width="100%">

**⬆️ That's it.** Watch the terminal above — clone → up → verify → dashboard. All local, all free.

```bash
git clone https://github.com/SunMe1977/HermesClawZero-ConfigSidecar.git
cd HermesClawZero-ConfigSidecar
docker compose --profile ollama up -d --build
```

**Open the Dashboard →** [`http://localhost:8010/dashboard`](http://localhost:8010/dashboard)  
*(Default login: `admin` / `HermesDash!2026` — change in `.env`)*

---

## 📊 Dashboard — Your Memory HQ

The **Dashboard** is the main entry point for everything your agent remembers:

| Feature | What you can do |
|---------|----------------|
| **Memory Timeline** | Browse all captured memories chronologically |
| **Semantic Search** | Find memories by meaning, not keywords |
| **Tenant Isolation** | Each scope/chat gets its own view — no data leaks |
| **Memory Types** | Filter by `conversation`, `skill`, `project`, `system` |
| **Quick Capture** | Manually add facts directly from the browser |
| **Export** | Download memory snapshots anytime |

> 🖼️ Dashboard live — screenshot below.

---

## 🧠 What It Does

| Problem | Without Sidecar | With Sidecar |
|---------|----------------|--------------|
| Session continuity | Agent forgets everything on restart | Remembers facts, preferences, decisions |
| Context cost | Long histories burn tokens | Semantic search finds *relevant* memories |
| Setup effort | Manual vector DB, embeddings, API keys | Docker + Ollama, zero config, zero cost |

**Workflow:** User ↔ Agent ↔ Sidecar API ↔ PostgreSQL + pgvector

---

## 🔧 CLI Tools (power users)

```bash
python memory.py capture "fact to remember"        # Save a memory
python memory.py search "query" 5                  # Search memories
python memory.py autosave "text" "backup.md"       # Backup a session
```

---

## 🧩 MCP Server

6 tools for Claude Desktop, Hermes, VS Code, and any MCP client:

```bash
pip install mcp requests
python mcp_server.py
```

| Tool | Description |
|------|-------------|
| `capture_memory` / `search_memory` | Read & write memories |
| `list_recent` / `memory_stats` | Browse & analyze |
| `delete_memory` / `review_memories` | Manage & synthesize |

**Register with Hermes:** `hermes mcp add hermesclawzero --command "python C:\dev\HermesClawZero-ConfigSidecar\mcp_server.py"`

---

## ⚙️ Advanced — Provider & Config

<details>
<summary>Click to expand</summary>

### Provider Support

| Mode | `AI_PROVIDER` | Embeddings | Key Required |
|------|---------------|------------|-------------|
| **Local (recommended)** | `ollama` | `nomic-embed-text` | None |
| OpenAI | `openai` | OpenAI | `OPENAI_API_KEY` |
| Gemini | `gemini` | Gemini | `GEMINI_API_KEY` |
| Anthropic | `anthropic` | Via embedding provider | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter` | OpenRouter | `OPENROUTER_API_KEY` |

### Compose Provider Override

```bash
COMPOSE_AI_PROVIDER=openrouter docker compose up -d --force-recreate api
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | — | Required for all protected endpoints |
| `DB_PASSWORD` | — | PostgreSQL password |
| `AI_PROVIDER` | `ollama` | `ollama` \| `openai` \| `gemini` \| `anthropic` \| `openrouter` |
| `MEM_PUBLIC_URL` | `http://localhost:8010` | Base URL for client scripts |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama endpoint |
| `AUTO_UPDATE_ENABLED` | `false` | Auto-pull from GitHub |
| `DASHBOARD_PASSWORD` | `HermesDash!2026` | Basic Auth for dashboard |

### Security

- Multi-tenant isolation via `chat_id` + `scope_id`
- API: `x-api-key` header or `?key=` query param
- Dashboard: Basic Auth
- Rate limiting: 30 req/min `/capture`, 60 req/min `/search`

</details>

---

## 🖼️ Dashboard Screenshot

![Dashboard](images/dashboard.png "Dashboard — memory timeline, search, and tenant isolation")

*The dashboard is available immediately at [`http://localhost:8010/dashboard`](http://localhost:8010/dashboard) after `docker compose up`.*

---

## 📦 What's Included

| Container | Port | Role |
|-----------|------|------|
| `hermesclawzero-configsidecar-api-1` | `:8010` | FastAPI + Dashboard + capture/search |
| `gbrain-postgres` | `:5666` | PostgreSQL 16 + pgvector |
| `gbrain-ollama` | `:11435` | Ollama (nomic-embed-text) |

**Health:** `curl http://localhost:8010/healthz`

---

## 📋 Roadmap

| Status | Feature |
|--------|---------|
| ✅ | PostgreSQL + pgvector, Docker, Dashboard, Multi-tenant, Semantic search, MCP server |
| ⬜ | Hybrid retrieval (lexical + vector fusion), Knowledge graph, Dashboard UI v2 |

---

## 🤝 Who Is This For?

AI developers · Hermes users · OpenClaw users · Self-hosters · MCP enthusiasts

---

Built for AI Agent autonomy.

<a href="https://github.com/nousresearch/hermes-agent"><img src="https://cdn.jsdelivr.net/gh/selfhst/icons/png/hermes-agent.png" alt="Hermes Agent" width="15%"></a>
<a href="https://github.com/openclaw/openclaw"><img src="https://openclaw.ai/logo.png" alt="OpenClaw" width="30%"></a>
<a href="https://ollama.com/"><img src="https://ollama.com/public/ollama.png" alt="Ollama" width="15%"></a>

- [Hermes Agent GitHub](https://github.com/nousresearch/hermes-agent)
- [OpenClaw Website](https://openclaw.ai)
- [Ollama Website](https://ollama.com)
