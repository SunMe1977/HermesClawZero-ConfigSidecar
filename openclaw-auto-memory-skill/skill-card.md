---
description: "Your agent remembers everything across sessions — no more repeating preferences, project context, or past decisions. Installs ready-to-use capture triggers so the agent saves facts automatically, loads context on new chats, and can back up session history on demand. Requires HermesClawZero Sidecar (local, private, PostgreSQL+pgvector)."
tags:
  - memory
  - hermes
  - persistence
  - vector-search
  - pgvector
  - embeddings
  - context
  - continuity
categories:
  - agents
  - knowledge
topics:
  - Memory
  - Vector Search
  - Chat Persistence
  - pgvector
  - Embeddings
  - Cross-Session Context
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
  - context-retention
  - session-continuity
  - agent-memory
---

# HermesClawZero Auto Memory

**Never repeat yourself again.** Your agent remembers preferences, project details, past decisions, and important facts across every conversation.

## What happens when you install?

1. **New chats start with context** — agent silently loads relevant past memories before responding
2. **Facts save automatically** — deterministic triggers capture preferences, instructions, project details, errors, corrections
3. **On-demand backups** — ask and the agent saves full session history
4. **Everything stays local** — your data never leaves your machine

## Who is this for?

| You want... | This skill does it |
|---|---|
| Agent to remember your name without re-introducing | ✅ Captured on first mention |
| Project context to carry between work sessions | ✅ Project details stored permanently |
| Not to re-explain your tech setup every chat | ✅ Config/preferences auto-saved |
| Full chat backups you can search later | ✅ autosave command |
| Privacy (no cloud memory) | ✅ Everything runs locally |

## Quick Example

```
You: "I'm working on DiskRaptor v0.3, UI tests are the main focus"
Agent: *silently captures: "DiskRaptor v0.3: focus on UI tests"*

Next session, you open a fresh chat:
Agent: *silently loads: "DiskRaptor v0.3: focus on UI tests"*
Agent: "Welcome back! Want to continue on DiskRaptor UI tests?"
```

## Requirements

- HermesClawZero Sidecar running (Docker)
- `MEM_PUBLIC_URL` + `API_KEY` configured in `.env`

## Repository

[github.com/SunMe1977/HermesClawZero-ConfigSidecar](https://github.com/SunMe1977/HermesClawZero-ConfigSidecar)
