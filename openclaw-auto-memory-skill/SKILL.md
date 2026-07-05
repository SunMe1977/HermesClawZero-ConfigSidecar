---
name: hermesclawzero-auto-memory
description: Automatically saves chat input to Hermes DB, loads context on fresh chat, and triggers DB maintenance.
---

# HermesClawZero Auto Memory

This skill integrates OpenClaw seamlessly with the HermesClawZero database to provide persistent, long-term memory across sessions, and manages automated database maintenance.

## 1. Auto-Load on Fresh Chat
Whenever a new session starts (e.g., first message of a thread), automatically load the user's profile and recent context to ensure continuity:
- Execute: `python scripts/memory.py search "user profile preferences current project state" 5`
- Silently absorb the retrieved context and use it to inform your response. Do not output the raw JSON to the user unless they ask.

## 2. Auto-Save (Continuous Memory)
To ensure all meaningful chat input is saved to the DB automatically:
- During the conversation, if the user shares a new fact, instruction, preference, or project detail, immediately capture it.
- Execute: `python scripts/memory.py capture "<Concise summary of the user's input or new fact>"`

## 3. Full Chat History Sync (Cron Backup)
If requested by the user, dump the current session history:
1. Use the `sessions_history` tool to get the current visible session.
2. Format the messages into a text summary.
3. Execute: `python scripts/memory.py autosave "<formatted_chat_history>" "chat_backup.md"`
HermesClawZero's watchdog will ingest it.

## 4. Automated Database Maintenance (Gardener & Reminders)
HermesClawZero requires periodic background maintenance to tag memory automatically and trigger push reminders to the user.

When you install or activate this skill, or when explicitly asked to "setup memory background jobs", you must create OpenClaw `cron` jobs to run these scripts automatically. Use the `cron` tool to schedule them:

**A. Nightly Memory Gardener (Auto-tagging):**
- Setup a cron job using the `cron` tool.
- Name: "hermes-memory-gardener"
- Expression: `0 3 * * *` (Runs every night at 3:00 AM)
- Payload: `agentTurn` that runs: `exec python C:\dev\HermesClawZero-ConfigSidecar\gardener.py`

**B. Daily Memory Highlight (Telegram Push):**
- Setup a cron job using the `cron` tool.
- Name: "hermes-daily-reminder"
- Expression: `0 9 * * *` (Runs every morning at 9:00 AM)
- Payload: `agentTurn` that runs: `exec python C:\dev\HermesClawZero-ConfigSidecar\daily_reminder.py`