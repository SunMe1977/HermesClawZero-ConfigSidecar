---
name: hermesclawzero-memory
description: Manages long-term semantic memory using HermesClawZero.
---

# HermesClawZero Memory Manager

Persistent, searchable memory via HermesClawZero Sidecar API.

## Script
`python C:\dev\HermesClawZero-ConfigSidecar\memory.py` — reads API config from `.env` or env vars automatically.

## Commands

### capture
Saves a fact/memory to global chat (always findable via default search):
`python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<text>" [scope_id]`

Do **not** pass a `chat_id` — memories go to `chat_id=global` and are always searchable.

### search
Retrieves relevant memories from global chat:
`python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "<query>" [limit=5]`

### autosave
Writes longer content as a single capture entry:
`python C:\dev\HermesClawZero-ConfigSidecar\memory.py autosave "<text>" [filename]`

## Auto-Load on Fresh Chat
When a new session starts, silently load context:
`python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "user profile preferences current project state" 5`

## Auto-Capture
During conversation, immediately capture new facts, instructions, preferences:
`python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "<summary>" [scope_id]`
