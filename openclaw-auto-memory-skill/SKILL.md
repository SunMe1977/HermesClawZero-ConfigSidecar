---
name: hermesclawzero-auto-memory
description: Automatically saves chat input to Hermes DB and loads context on fresh chat.
---

# HermesClawZero Auto Memory

This skill integrates OpenClaw seamlessly with the HermesClawZero database to provide persistent, long-term memory across sessions.

## 1. Auto-Load on Fresh Chat
Whenever a new session starts (e.g., first message of a thread), you must automatically load the user's profile and recent context to ensure continuity:
- Execute: `python scripts/memory.py search "user profile preferences current project state" 5`
- Silently absorb the retrieved context and use it to inform your response. Do not output the raw JSON to the user unless they ask.

## 2. Auto-Save (Continuous Memory)
To ensure all meaningful chat input is saved to the DB automatically:
- During the conversation, if the user shares a new fact, instruction, preference, or project detail, you must immediately capture it.
- Execute: `python scripts/memory.py capture "<Concise summary of the user's input or new fact>"`

## 3. Full Chat History Sync (Cron Backup)
If the user requests a full backup of the chat or as a periodic heartbeat action, you can dump the current session history:
1. Use the `sessions_history` tool to get the current visible session.
2. Format the messages into a text summary.
3. Execute: `python scripts/memory.py autosave "<formatted_chat_history>" "chat_backup.md"`
HermesClawZero's watchdog will automatically detect the file in the `sync/` directory and ingest it into the vector database.
