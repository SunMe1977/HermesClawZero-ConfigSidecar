```text
    __  __                                  ________                
   / / / /__  _________ ___  ___  _____    / ____/ /___ _      ______
  / /_/ / _ \/ ___/ __ `__ \/ _ \/ ___/   / /   / / __ `/ | /| / / __ \
 / __  /  __/ /  / / / / / /  __(__  )   / /___/ / /_/ /| |/ |/ / /_/ /
/_/ /_/\___/_/  /_/ /_/ /_/\___/____/    \____/_/\__,_/ |__/|__/\____/ 
                                                                       
                     ConfigSidecar - Persistent Memory
```

The **HermesClawZero-ConfigSidecar** is a modular, automation-first sidecar service designed to add persistent long-term memory to AI agents like Hermes. 

## Quick Start (One-Click Setup)

No manual configuration needed. Just run the setup script for your OS. It will verify Python, Docker, install dependencies, and generate your `.env` file automatically.

### Windows
1. Double-click **`setup.bat`**.

### Linux/macOS
1. Run **`bash setup.sh`**.

*(After the first run, update the `OPENCLAW_KEY` in the generated `.env` file with your secret.)*

## The Architecture
The project follows a decoupled "sidecar" pattern:
1.  **Capture**: The agent (Hermes) appends session summaries and significant findings to a local `sync/` directory.
2.  **Sync (The Watchdog)**: A background service (`memory_sync.py`) monitors the `sync/` directory and `inbox/`. When new files appear, it automatically ingests them.
3.  **Persistence**: The content is posted to a remote or local **OpenClaw** memory service (vector store), making your agent's history queryable via semantic search.

## Tools
This project provides a robust CLI (`scripts/memory.py`), a drag-and-drop ingest tool, and maintenance utilities:

- **Ingest**: Drag and drop any file onto `ingest.bat` to automatically move it to `inbox/` for processing.
- **Maintenance**: Run `maintenance.bat` to trigger embedding rebuilds after large data imports.
- **Capture**: `python scripts/memory.py capture "The user prefers to work in C:/dev/"`
- **Search**: `python scripts/memory.py search "where does the user work?"`
- **Autosave**: `python scripts/memory.py autosave "content..." "filename.txt"`

## Big Data Best Practices
- **Archive**: All successfully ingested files are automatically moved to the `archive/` folder.
- **Deduplication**: The backend (`main.py`) automatically performs semantic similarity checks to prevent duplicate entries.
- **Maintenance**: Regularly run `maintenance.bat` if you have imported large batches of data.

## Troubleshooting
- **401 Unauthorized**: Ensure your `OPENCLAW_KEY` in `.env` matches the server secret.
- **Sync service not running**: Check if `memory_sync.py` is active in your process monitor.
- **Log missing from memory**: Verify that the file was written to the `sync/` directory. If it remains there, the watchdog process needs to be restarted.

---
*Built with ❤️ for AI Agent autonomy.*

[![Hermes Agent](https://avatars.githubusercontent.com/u/108990526?s=64&v=4)](https://github.com/nousresearch/hermes-agent)
[![OpenClaw](https://openclaw.ai/logo.png)](https://openclaw.ai)
