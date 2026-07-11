---
name: hermesclawzero-auto-memory
description: Auto-capture chat to Hermes DB, load context on fresh chats, scheduled DB maintenance.
---

# HermesClawZero Auto Memory

Automatically saves meaningful chat input to HermesClawZero DB, loads user context on fresh sessions, and manages automated DB maintenance.

## Script Path
`C:\dev\HermesClawZero-ConfigSidecar\memory.py` — reads API config from `.env` or env vars automatically.

## 1. Auto-Load on Fresh Chat
When a new session starts, load user context:
- Execute: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "user profile preferences current project state" 5`
- Silently absorb context. Do not output raw JSON unless asked.

## 2. Auto-Capture
During conversation, if the user shares a new fact, instruction, preference, or project detail, immediately capture it:
- Execute: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<concise summary>" [scope_id]`
- Do **not** pass a `chat_id` — that makes memories unfindable via default search. Omitting it stores in `chat_id=global`.

## 3. Full Chat History Sync (On Request)
If the user asks to back up the session:
1. Use `sessions_history` to get the current session.
2. Format into text summary.
3. Execute: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py autosave "<formatted_chat_history>" "chat_backup.md"`

## 4. Automated Database Maintenance
Cron jobs for background memory maintenance.

**A. Nightly Memory Gardener (3:00 AM):**
- `exec python C:\dev\HermesClawZero-ConfigSidecar\gardener.py`

**B. Daily Memory Highlight (9:00 AM):**
- `exec python C:\dev\HermesClawZero-ConfigSidecar\daily_reminder.py`
