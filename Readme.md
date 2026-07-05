# HermesClawZero-ConfigSidecar

The **HermesClawZero-ConfigSidecar** is a modular, automation-first sidecar service designed to add persistent long-term memory to AI agents like Hermes. 

Instead of forcing your agent to manage its own complex database connections, this sidecar handles the heavy lifting of synchronizing session data, chat logs, and configuration context into a searchable vector memory service.

## The Architecture
The project follows a decoupled "sidecar" pattern:
1.  **Capture**: The agent (Hermes) appends session summaries and significant findings to a local `sync/` directory.
2.  **Sync (The Watchdog)**: A background service (`memory_sync.py`) monitors the `sync/` directory. When new files appear, it automatically ingests them.
3.  **Persistence**: The content is posted to a remote or local **OpenClaw** memory service (vector store), making your agent's history queryable via semantic search.

## Key Features
- **Decoupled Design**: The agent never talks directly to the DB; it writes files to a local directory.
- **Automation-First**: Setup scripts ensure your environment is configured correctly, including automated `.env` generation.
- **Resilient**: If the sync service stops, logs just pile up in the `sync/` directory; as soon as you restart the service, it catches up.
- **Queryable Memory**: Use the provided `memory.py` CLI to perform semantic searches against your agent's historical context.

## Quick Start

### 1. Requirements
- Python 3.11+
- Git

### 2. Setup
Clone the repo and initialize your environment:

```bash
git clone https://github.com/SunMe1977/HermesClawZero-ConfigSidecar.git
cd HermesClawZero-ConfigSidecar
# Run your setup script
./setup.bat  # Or setup.sh on Linux
```

### 3. Configuration
Copy the `.env.example` to `.env` and configure your credentials:

```bash
OPENCLAW_URL=https://your-memory-service.com
OPENCLAW_KEY=your-secret-key
OPENCLAW_SYNC_DIR=./sync/
```

### 4. Running the Sync Watchdog
To keep your memory current, ensure the sync service is running:

```bash
python memory_sync.py
```

## Tools
This project provides a robust CLI (`scripts/memory.py`) for interacting with the service:

- **Capture**: `python scripts/memory.py capture "The user prefers to work in C:/dev/"`
- **Search**: `python scripts/memory.py search "where does the user work?"`
- **Autosave**: `python scripts/memory.py autosave "content..." "filename.txt"`

## Troubleshooting
- **401 Unauthorized**: Ensure your `OPENCLAW_KEY` in `.env` matches the server secret.
- **Sync service not running**: Check if `memory_sync.py` is active in your process monitor.
- **Log missing from memory**: Verify that the file was written to the `sync/` directory. If it remains there, the watchdog process needs to be restarted.

---
*Built with ❤️ for AI Agent autonomy.*
