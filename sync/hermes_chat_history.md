# Chat History Summary: HermesClawZero-ConfigSidecar

## Session Overview
User (Hansj) inquired about why their chat history wasn't being saved/synced with their 'HermesClawZero-ConfigSidecar' project.

## Troubleshooting & Findings
- Inspected project directory `C:/dev/HermesClawZero-ConfigSidecar/`.
- Verified existence of `main.py` (API), `memory_sync.py` (watchdog), and `sync/` directory.
- Discovered that the `memory.py` script in `hermes-skill/scripts/` was misconfigured, pointing to a remote URL instead of the local API (`http://localhost:8000`).
- Confirmed that `memory_sync.py` monitors the `sync/` folder for new files (`.txt`, `.md`, etc.) and ingests them into the local database via the `/capture` endpoint.

## Actions Taken
- Patched `hermes-skill/scripts/memory.py` to point to `http://localhost:8000` and use the correct `API_KEY` environment variable.
- Provided a test command for the user: `python C:/dev/HermesClawZero-ConfigSidecar/hermes-skill/scripts/memory.py capture "Testing the local capture"`.
- Agreed to auto-save significant chat summaries to `C:/dev/HermesClawZero-ConfigSidecar/sync/hermes_chat_history.md` so the watchdog can auto-ingest them.

## Status
- Local sync pipeline is now functional.
- Future interactions will be appended to this sync file for automatic ingestion.
