import time
import sqlite3
import os
import requests
import pathlib

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv():
        return False

def _load_env_file_fallback(path: str = ".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lstrip("\ufeff")
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_dotenv()
_load_env_file_fallback()

# Configuration
def _default_hermes_db_path() -> str:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        windows_candidates = [
            os.path.join(local_appdata, "hermes", "state.db"),
            os.path.join(os.path.expanduser("~"), ".hermes", "state.db"),
        ]
        for candidate in windows_candidates:
            if os.path.exists(candidate):
                return candidate
        return windows_candidates[0]

    home = os.path.expanduser("~")
    linux_candidates = [
        os.path.join(home, ".hermes", "state.db"),
        os.path.join(home, ".local", "share", "hermes", "state.db"),
        os.path.join(home, "hermes", "state.db"),
    ]
    for candidate in linux_candidates:
        if os.path.exists(candidate):
            return candidate

    # Fallback for logs and manual configuration when no known default exists.
    return linux_candidates[0]


DB_PATH = os.getenv("HERMES_DB_PATH", _default_hermes_db_path())
BASE_URL = os.getenv("MEM_PUBLIC_URL") or os.getenv("OPENCLAW_URL") or "http://localhost:8010"
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

    if not API_KEY:
        print("[WATCHDOG] Disabled: API_KEY is not set in .env. Set API_KEY to enable watchdog sync.")
        raise SystemExit(0)

    if not os.path.exists(DB_PATH):
        print(f"[WATCHDOG] Disabled: Hermes DB not found at {DB_PATH}. Set HERMES_DB_PATH to enable watchdog sync.")
        raise SystemExit(0)

    while True:
        try:
            sync_messages()
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}")
        time.sleep(10)
