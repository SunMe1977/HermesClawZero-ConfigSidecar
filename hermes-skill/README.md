# HermesClawZero Auto Memory

[![ClawHub](https://img.shields.io/badge/ClawHub-hermesclawzero--auto--memory-blue)](https://clawhub.ai/sunme1977/skills/hermesclawzero-auto-memory)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Persist conversation context across OpenClaw sessions using **HermesClawZero** — a local PostgreSQL+pgvector memory backend.

## Features

| Capability | Description |
|---|---|
| **Auto-Capture** | Saves new facts, preferences, and project details automatically |
| **Auto-Load** | Retrieves relevant memories when a new session starts |
| **Semantic Search** | Hybrid vector + lexical search via pgvector |
| **Chat Backup** | On-demand full-session history snapshots |
| **DB Maintenance** | Optional nightly tagging + daily reminders (cron) |

All processing stays **local** — no third-party data sharing.

## Requirements

| Component | Version |
|---|---|
| Python | 3.10+ |
| HermesClawZero Sidecar | Running (Docker) |
| PostgreSQL | 15+ with pgvector |
| Ollama / OpenAI | For embeddings |
| OpenClaw | Workspace with skills support |

## Quick Start

### 1. Install the skill

```bash
openclaw skills install hermesclawzero-auto-memory
```

### 2. Configure environment

Create a `.env` file with your Sidecar connection details:

```bash
MEM_PUBLIC_URL=http://localhost:8010
API_KEY=your_api_key_here
```

### 3. Verify connectivity

```bash
python scripts/smoke_test.ps1
```

Or run a manual test:

```bash
python scripts/memory.py search "hello world" 3
```

## Usage

### Capture a memory

```bash
python scripts/memory.py capture "User prefers dark mode in all UIs" ui_preferences
```

### Search memories

```bash
python scripts/memory.py search "dark mode preferences" 5
```

### Autosave session history

```bash
python scripts/memory.py autosave "$(cat session_backup.md)" "session_backup.md"
```

## Skill Structure

```
hermesclawzero-auto-memory/
├── SKILL.md              # OpenClaw skill instructions
├── skill-card.md         # ClawHub marketplace card
├── permissions.yaml      # Declared permissions
├── secrets.yaml          # Declared secrets
├── README.md             # This file
├── CHANGELOG.md          # Version history
└── scripts/
    ├── memory.py         # CLI tool (capture/search/autosave)
    ├── install.ps1       # Install verification script
    ├── update.ps1        # Update check script
    └── smoke_test.ps1    # Integration smoke tests
```

## Roadmap

- [ ] Support for chat-scoped isolation
- [ ] Multi-user memory namespaces
- [ ] WebUI dashboard for memory browsing
- [ ] Scheduled memory expiry policies

## License

MIT — see [LICENSE](LICENSE).
