import time
import sqlite3
import os
import requests
import pathlib
from dotenv import load_dotenv

load_dotenv()

# Configuration
_default_db = os.path.join(os.getenv("LOCALAPPDATA", ""), "hermes", "state.db")
DB_PATH = os.getenv("HERMES_DB_PATH", _default_db)
BASE_URL = os.getenv("MEM_PUBLIC_URL") or os.getenv("OPENCLAW_URL") or "http://localhost:8000"
API_URL = BASE_URL.rstrip("/") + "/capture"
API_KEY = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
LAST_ID_FILE = pathlib.Path("sync_last_id.txt")

def get_last_synced_id():
    if LAST_ID_FILE.exists():
        return int(LAST_ID_FILE.read_text())
    return 0

def save_last_synced_id(msg_id):
    LAST_ID_FILE.write_text(str(msg_id))

def sync_messages():
    if not API_KEY:
        print("[WATCHDOG] API_KEY is not set. Skipping sync.")
        return

    if not os.path.exists(DB_PATH):
        print(f"[WATCHDOG] DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    last_id = get_last_synced_id()
    
    # Query for new messages
    cursor.execute("SELECT id, content, role FROM messages WHERE id > ?", (last_id,))
    rows = cursor.fetchall()
    
    for row in rows:
        msg_id, content, role = row
        if content and role in ['user', 'assistant']:
            print(f"[WATCHDOG] Syncing message {msg_id}")
            try:
                requests.post(API_URL, params={"key": API_KEY}, json={"text": f"[{role}]: {content}"}, timeout=30)
                save_last_synced_id(msg_id)
            except Exception as e:
                print(f"[WATCHDOG] Sync error: {e}")
    
    conn.close()

if __name__ == "__main__":
    print("[WATCHDOG] Starting memory sync service...")
    while True:
        try:
            sync_messages()
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}")
        time.sleep(10)
