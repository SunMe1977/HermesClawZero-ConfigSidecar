---
name: "hermesclawzero-auto-memory"
description: "Auto-capture chat to Hermes DB, load context on fresh chats, scheduled DB maintenance."
version: "1.1.0"
tags:
  - memory
  - hermes
  - persistence
  - vector-search
categories:
  - agents
  - knowledge
topics:
  - Memory
  - Vector Search
  - Chat Persistence
  - pgvector
  - Embeddings
---

# HermesClawZero Auto Memory

Automatically saves meaningful chat input to HermesClawZero DB, loads user context on fresh sessions, and manages automated DB maintenance.

## ⚠️ Privacy & Consent

This skill **captures and persistently stores conversation content** in a local PostgreSQL+pgvector database. By activating this skill you consent to automatic memory capture.

- **Data stays local** — all processing runs via the Sidecar API on your machine; no third-party data sharing
- **Background jobs** (cron) are only created when you explicitly ask to set them up
- **Captures only meaningful facts** — the agent summaries and saves key information, not raw chat logs

## Script Path

The `memory.py` CLI lives alongside the sidecar at:
`C:\dev\HermesClawZero-ConfigSidecar\memory.py`

It reads API config from `.env` or env vars (`MEM_PUBLIC_URL`, `API_KEY`) automatically.

## 1. Auto-Load on Fresh Chat

When a new session starts, load the user's profile and recent context:
- Execute: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "user profile preferences current project state" 5`
- Silently absorb the context. Do not output raw JSON to the user unless they ask.

## 2. Auto-Save (Continuous Capture)

During conversation, if the user shares a new fact, instruction, preference, project detail, or anything worth remembering, immediately capture it:
- Execute: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<Concise summary of the user's input or new fact>"`
- Optionally pass a `scope_id` (e.g. project name) for logical grouping.
- Do **not** pass a `chat_id` — that makes memories unfindable via default search. Omitting it stores in `chat_id=global`.

## 3. Full Chat History Sync (On Request)

If the user asks to back up the current session:
1. Use `sessions_history` to get the current visible session.
2. Format messages into a text summary.
3. Execute: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py autosave "<formatted_chat_history>" "chat_backup.md"`

## 4. Automated Database Maintenance (Cron Jobs)

HermesClawZero requires periodic background jobs for tagging memories and push reminders.

When the user asks to "setup memory background jobs", create these cron jobs:

**A. Nightly Memory Gardener (Auto-tagging):**
- Name: `hermes-memory-gardener`
- Expression: `0 3 * * *` (3:00 AM daily)
- Session target: isolated
- AgentTurn message: `exec python C:\dev\HermesClawZero-ConfigSidecar\gardener.py`

**B. Daily Memory Highlight (Telegram Push):**
- Name: `hermes-daily-reminder`
- Expression: `0 9 * * *` (9:00 AM daily)
- Session target: isolated
- AgentTurn message: `exec python C:\dev\HermesClawZero-ConfigSidecar\daily_reminder.py`

Current cron jobs already exist for both (checked during setup).
