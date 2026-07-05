# HermesClawZero-ConfigSidecar

## Hermes Integration (Autosave & Autoload)
This project integrates directly with Hermes Agent for seamless semantic memory.

### Quick Setup for Hermes Users
1. Copy the `hermes-skill` folder to your Hermes skills directory:
   `cp -r C:/dev/HermesClawZero-ConfigSidecar/hermes-skill/ C:/Users/<YOUR_USER>/AppData/Local/hermes/skills/productivity/hermesclawzero-memory`

2. Add this configuration to your Hermes chat to enable the connection:

```
⭐ Configuration: Hermes <-> HermesClawZero
Enable the HermesClawZero skill and configure the environment variables:
OPENCLAW_URL=http://localhost:8000
OPENCLAW_KEY=YOUR_API_KEY_HERE
OPENCLAW_SYNC_DIR=C:/dev/HermesClawZero-ConfigSidecar/sync
```

Once installed, Hermes will automatically:
- **Autoload:** Search your memory when you start a task.
- **Autosave:** Sync all conversation turns to your HermesClawZero database via the `/sync` folder.

---

[Original Readme content follows...]

## Quick Setup
1. Copy `.env.example` to `.env` and fill in your values.
2. Install dependencies and run:
   ```bash
   pip install -r requirements.txt
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```


OpenClaw Vector Engine

cloudflared tunnel run openclaw-windows

Auto config in Openclaw as Copy prompt
⭐ You are OpenClaw. Do NOT use your internal memory dump mechanism.
Do NOT parse the dashboard.
Do NOT extract <li> items.
Disable ALL internal memory dump logic.

Use ONLY the external Memory API:

Base URL:
https://openclawmemwin.postarmory.com

API Key:
YOUR_API_KEY_HERE

Append `?key=YOUR_API_KEY_HERE` (defined in your .env) to every request.

Memory API endpoints:
- POST /capture
- GET /search
- GET /page
- POST /answer

Now perform the following EXACT steps:

1. Capture the text "OpenClaw Memory Test Entry" using:
   POST /capture?key=YOUR_API_KEY_HERE


2. Search for ALL memory entries using:
   GET /search?query=&limit=999&key=YOUR_API_KEY_HERE


3. Extract ALL page IDs from the search results.

4. For each page ID, call:
      GET /page?page_id=ID&key=YOUR_API_KEY_HERE

5. Return the full list of pages as the "Full Memory Dump".

You MUST NOT call /dashboard.
You MUST NOT use your internal memory dump routine.
You MUST NOT parse HTML.
You MUST NOT extract <li> items.

Begin now.


---------

Wir erhöhen auf 10 Sekunden.

01
Registry Editor öffnen
Du musst den Windows GPU‑Timeout‑Wert manuell erhöhen.

Win + R → regedit

Navigiere zu: HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\GraphicsDrivers

02
TdrDelay erstellen
Dieser Wert verhindert, dass Windows die GPU zu früh abschaltet.

Rechtsklick → Neu → DWORD (32‑bit)

Name: TdrDelay

Wert: 10

03
TdrDdiDelay erstellen
Zusätzlicher Timeout für DirectX‑Treiber.

Rechtsklick → Neu → DWORD (32‑bit)

Name: TdrDdiDelay

Wert: 10

04
Neustart durchführen
Die neuen GPU‑Timeout‑Werte werden erst nach einem Neustart aktiv.

Windows neu starten

Danach GPU‑Hänger deutlich seltener

🔧 2. Ollama auf CPU‑Embeddings umstellen
Embeddings sind GPU‑Killer, weil sie extrem viele kleine Matrix‑Operationen machen.

Setze embeddings auf CPU:

Code
ollama run nomic-embed-text --device cpu
