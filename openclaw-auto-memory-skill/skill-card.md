---
description: "Automatically saves chat context to HermesClawZero persistent memory. Captures facts, loads history on new sessions, and maintains the DB via background jobs. Requires explicit env-config (MEM_PUBLIC_URL + API_KEY) and user approval for cron setup."
tags:
  - memory
  - hermes
  - persistence
  - chat
categories:
  - memory
  - automation
---

# HermesClawZero Auto Memory

Persist conversation context across sessions using HermesClawZero.

## What it does

- **Auto-Capture** — saves new facts, instructions, and project details to a PostgreSQL+pgvector database
- **Auto-Load** — retrieves relevant memories when a new session starts
- **Chat Backup** — on-demand session history snapshots
- **DB Maintenance** — optional nightly tagging + daily reminders (user must enable)

## Requirements

- HermesClawZero ConfigSidecar running (Docker) with PostgreSQL + pgvector
- `MEM_PUBLIC_URL` and `API_KEY` set in `.env` or environment
- Ollama or other embedding provider accessible

## Privacy

This skill **persists conversation content** to a local database. It does **not** send data to third parties. All processing runs locally via the Sidecar API.

## Installation

```
openclaw skills install hermesclawzero-auto-memory
```

Then configure your `.env` and ensure the Sidecar is running.
