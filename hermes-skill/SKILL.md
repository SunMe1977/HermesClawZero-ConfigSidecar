---
name: "hermesclawzero-auto-memory"
description: "Auto-capture chat to Hermes DB, load context on fresh chats, scheduled DB maintenance."
version: "1.2.0"
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

Automatically captures conversation context to HermesClawZero DB so the agent remembers across sessions. Loads relevant memories on fresh chats and supports scheduled DB maintenance.

## ⚠️ Privacy & Consent

By activating this skill you consent to automatic memory capture.

- **Data stays local** — all processing runs via the Sidecar API on your machine; no third-party data sharing  
- **Captures structured facts** — the agent summaries and stores key information, not raw chat logs
- **Cron jobs** (nightly gardener, daily reminder) are only created when you explicitly ask to set them up

## Script Path

```bash
# The CLI lives alongside the sidecar, NOT in the skill directory:
python C:\dev\HermesClawZero-ConfigSidecar\memory.py <command> "<text>"
```

Config is read from `.env` or env vars (`MEM_PUBLIC_URL`, `API_KEY`) automatically.

---

## 1. Auto-Load on Fresh Chat (Session Start)

**When:** Every new session begins (you receive context that it's a fresh chat).

**What to do:**
1. Silently run: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "user profile preferences current project state" 5`
2. Absorb the returned context internally.
3. **Do not** output raw JSON or mention the search to the user unless they explicitly ask.

---

## 2. Auto-Capture — Deterministic Triggers

**When to capture** (run `memory.py capture "..."` immediately):

| Trigger | Example | What to capture |
|---|---|---|
| **New fact stated** | "I live in Berlin" | `"User lives in Berlin"` |
| **Preference revealed** | "I prefer dark mode" | `"User prefers dark mode in all UIs"` |
| **Instruction given** | "Call me Hans" | `"User's name is Hans, goes by Hans"` |
| **Project detail shared** | "Working on DiskRaptor v0.3" | `"Current project: DiskRaptor v0.3, focus on UI tests"` |
| **Decision made** | "Let's go with PostgreSQL" | `"Chose PostgreSQL for the data layer"` |
| **Error/blocker mentioned** | "The build fails on Windows" | `"DiskRaptor: build fails on Windows, needs investigation"` |
| **Tool/config change** | "I set up Ollama on port 11435" | `"Ollama configured on port 11435"` |
| **User corrects you** | "No, the port is 8080 not 3000" | `"Corrected: server runs on port 8080"` |

**How to capture:**
```bash
python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<concise summary of the fact>"
# Optional: pass a scope_id for logical grouping
python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<fact>" "project_name"
```

**Do NOT capture when:**
- User is just making small talk ("hello", "thanks", "ok")
- User is asking a question (capture the *answer/fact*, not the question)
- User is giving multi-turn instructions that aren't final yet (wait for resolution)
- The information is already captured (avoid duplicates)
- The information is temporary/throwaway ("let me try something real quick")

**Rule of thumb:** If you'd want to remember this when a *different session* starts next week → capture it. If it's only useful right now → skip it.

---

## 3. Full Chat History Sync (On Request)

**When:** User explicitly asks to back up the session ("save this chat", "backup session").

**What to do:**
1. Use `sessions_history` to get the current visible session.
2. Format messages into a text summary.
3. Execute: `python C:\dev\HermesClawZero-ConfigSidecar\memory.py autosave "<formatted_chat_history>" "chat_backup.md"`

---

## 4. Automated Database Maintenance (Cron Jobs)

**When:** User asks to "setup memory background jobs" or "enable memory maintenance".

Create these cron jobs:

**A. Nightly Memory Gardener (Auto-tagging):**
- Name: `hermes-memory-gardener`
- Expression: `0 3 * * *` (daily 3:00 AM)
- Session target: isolated
- AgentTurn message: `exec python C:\dev\HermesClawZero-ConfigSidecar\gardener.py`

**B. Daily Memory Highlight (Telegram Push):**
- Name: `hermes-daily-reminder`
- Expression: `0 9 * * *` (daily 9:00 AM)
- Session target: isolated
- AgentTurn message: `exec python C:\dev\HermesClawZero-ConfigSidecar\daily_reminder.py`

Current cron jobs already exist for both (checked during setup).

---

## Quick Reference

```bash
# Load context (session start, silent):
python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "user profile preferences current project state" 5

# Save a fact (deterministic triggers above):
python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<fact>" [scope_id]

# Backup session (on request):
python C:\dev\HermesClawZero-ConfigSidecar\memory.py autosave "<text>" [filename]
```
