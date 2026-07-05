# OpenClaw Auto Memory Skill for HermesClawZero

This is a plug-and-play skill for [OpenClaw](https://github.com/openclaw/openclaw) that allows the AI agent to automatically save and retrieve semantic memory from a local HermesClawZero instance.

## Features
- **Auto-Load**: The agent will automatically query the Hermes database on a fresh chat to restore context about the user and current projects.
- **Auto-Save**: The agent will actively capture new facts, preferences, and important chat inputs and send them to the Hermes database.
- **Full Sync**: The agent can periodically dump full chat session histories into the `sync/` directory for background vector ingestion.

## Installation for OpenClaw
1. Copy the `openclaw-auto-memory-skill` folder into your OpenClaw skills directory (e.g., `~/.openclaw/plugin-skills/hermes-memory/`).
2. Ensure you have the `.env` variables available in your OpenClaw environment, or fallback variables set in OpenClaw config:
   - `MEM_PUBLIC_URL` (or `OPENCLAW_URL`)
   - `API_KEY` (or `OPENCLAW_KEY`)
   - `MEM_SYNC_DIR` (or `OPENCLAW_SYNC_DIR`)
3. OpenClaw will automatically detect the `SKILL.md` file and apply the memory behaviors.

## How it works
The `SKILL.md` contains strict behavioral prompts that instruct the OpenClaw agent to execute `python scripts/memory.py` in the background for `search`, `capture`, and `autosave` commands based on conversational context. No extra core code modifications to OpenClaw are required.
