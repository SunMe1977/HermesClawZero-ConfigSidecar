---
description: "Automatically saves chat context to HermesClawZero persistent memory via PostgreSQL+pgvector. Captures facts, loads history on new sessions, and maintains the DB via background jobs. Requires explicit env-config (MEM_PUBLIC_URL + API_KEY) and user approval for cron setup."
tags:
  - memory
  - hermes
  - persistence
  - vector-search
  - pgvector
  - embeddings
categories:
  - agents
  - knowledge
topics:
  - Memory
  - Vector Search
  - Chat Persistence
  - pgvector
  - Embeddings
keywords:
  - memory
  - hermes-claw-zero
  - persistence
  - vector-search
  - semantic-memory
  - chat-history
  - openclaw-memory
  - pgvector-store
  - conversation-context
---

# HermesClawZero Auto Memory

Persist conversation context across sessions using HermesClawZero — a local, privacy-first memory backend.

## Quick Overview

- **Auto-Capture** — saves new facts, instructions, and project details automatically
- **Auto-Load** — retrieves relevant context when a new session starts  
- **Semantic Search** — hybrid vector + lexical search via pgvector
- **Privacy-first** — all data stays local; no third-party data sharing
- **Cron jobs** — optional; user must explicitly enable

## Architecture

```
OpenClaw Agent → memory.py (CLI) → HermesClawZero Sidecar (FastAPI)
                                          → PostgreSQL + pgvector
                                          → Ollama (embeddings via nomic-embed-text)
```

## Use Cases

| Scenario | How it helps |
|---|---|
| **Cross-session continuity** | Agent remembers user preferences across chats |
| **Project context** | Project details persist between work sessions |
| **Knowledge base** | Store and retrieve facts via semantic search |
| **Session backup** | Full chat history snapshots on demand |

## Installation

```bash
openclaw skills install hermesclawzero-auto-memory
```

Then configure `.env` with `MEM_PUBLIC_URL` and `API_KEY`.

## Repository

Source: [github.com/SunMe1977/HermesClawZero-ConfigSidecar](https://github.com/SunMe1977/HermesClawZero-ConfigSidecar)
