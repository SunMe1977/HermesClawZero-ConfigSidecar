import time
import sqlite3
import os
import requests
import pathlib
import json

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
FAILED_IDS_FILE = pathlib.Path("sync_failed_ids.json")
MAX_MESSAGES_PER_CYCLE = int(os.getenv("WATCHDOG_MAX_MESSAGES_PER_CYCLE", "50"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_REQUEST_TIMEOUT_SECONDS", "20"))
MAX_RETRIES_PER_MESSAGE = int(os.getenv("WATCHDOG_MAX_RETRIES_PER_MESSAGE", "5"))
STATUS_URL = BASE_URL.rstrip("/") + "/watchdog/status"
IDLE_LOG_INTERVAL_SECONDS = int(os.getenv("WATCHDOG_IDLE_LOG_INTERVAL_SECONDS", "60"))
LAST_IDLE_LOG_TS = 0


def _api_reachable() -> bool:
    health_url = BASE_URL.rstrip("/") + "/healthz"
    try:
        resp = requests.get(health_url, timeout=REQUEST_TIMEOUT_SECONDS)
        return resp.status_code < 500
    except Exception:
        return False


def _post_watchdog_status(last_synced_id: int, latest_source_id: int):
    pending = max(0, latest_source_id - last_synced_id)
    try:
        requests.post(
            STATUS_URL,
            params={"key": API_KEY},
            json={
                "pending": pending,
                "last_synced_id": int(last_synced_id),
                "latest_source_id": int(latest_source_id),
            },
            timeout=min(8, REQUEST_TIMEOUT_SECONDS),
        )
    except Exception:
        # Status publishing is best-effort and must never stop syncing.
        pass

def get_last_synced_id():
    if LAST_ID_FILE.exists():
        return int(LAST_ID_FILE.read_text())
    return 0

def save_last_synced_id(msg_id):
    LAST_ID_FILE.write_text(str(msg_id))


def _load_failed_id_counts() -> dict[int, int]:
    if not FAILED_IDS_FILE.exists():
        return {}
    try:
        data = json.loads(FAILED_IDS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        parsed: dict[int, int] = {}
        for key, value in data.items():
            try:
                parsed[int(key)] = int(value)
            except Exception:
                continue
        return parsed
    except Exception:
        return {}


def _save_failed_id_counts(counts: dict[int, int]):
    serializable = {str(k): int(v) for k, v in counts.items() if int(v) > 0}
    FAILED_IDS_FILE.write_text(json.dumps(serializable, ensure_ascii=True, sort_keys=True), encoding="utf-8")


def _clear_failed_id(counts: dict[int, int], msg_id: int):
    if msg_id in counts:
        counts.pop(msg_id, None)
        _save_failed_id_counts(counts)

def sync_messages():
    global LAST_IDLE_LOG_TS

    if not _api_reachable():
        print(f"[WATCHDOG] API not reachable at {BASE_URL}. Will retry next cycle.")
        return

    failed_counts = _load_failed_id_counts()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    last_id = get_last_synced_id()

    cursor.execute("SELECT COALESCE(MAX(id), 0) FROM messages")
    latest_source_id = int(cursor.fetchone()[0] or 0)
    _post_watchdog_status(last_id, latest_source_id)
    
    # Query for new messages
    cursor.execute(
        "SELECT id, content, role FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
        (last_id, MAX_MESSAGES_PER_CYCLE),
    )
    rows = cursor.fetchall()

    if not rows:
        now_ts = int(time.time())
        if now_ts - LAST_IDLE_LOG_TS >= max(1, IDLE_LOG_INTERVAL_SECONDS):
            pending = max(0, latest_source_id - last_id)
            print(
                f"[WATCHDOG] Idle: no new messages (last_synced_id={last_id}, "
                f"latest_source_id={latest_source_id}, pending={pending})"
            )
            LAST_IDLE_LOG_TS = now_ts
        conn.close()
        return
    
    for row in rows:
        msg_id, content, role = row
        if content and role in ['user', 'assistant']:
            print(f"[WATCHDOG] Syncing message {msg_id}")
            try:
                resp = requests.post(
                    API_URL,
                    params={"key": API_KEY},
                    json={"text": f"[{role}]: {content}"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                save_last_synced_id(msg_id)
                _post_watchdog_status(msg_id, latest_source_id)
                _clear_failed_id(failed_counts, msg_id)
            except requests.HTTPError:
                status_code = resp.status_code if "resp" in locals() else 0
                response_preview = ""
                if "resp" in locals():
                    response_preview = (resp.text or "").strip().replace("\n", " ")[:500]

                lower_preview = response_preview.lower()
                if status_code in (401, 403) or "invalid_api_key" in lower_preview or "incorrect api key" in lower_preview:
                    print(
                        "[WATCHDOG] Disabled: capture authentication failed (invalid provider key or provider mismatch). "
                        "Fix .env provider/key settings, then restart start.sh/start.bat."
                    )
                    raise SystemExit(0)

                failed_counts[msg_id] = failed_counts.get(msg_id, 0) + 1
                _save_failed_id_counts(failed_counts)

                print(
                    f"[WATCHDOG] Sync HTTP error on message {msg_id}: status={status_code}, "
                    f"attempt={failed_counts[msg_id]}/{MAX_RETRIES_PER_MESSAGE}, detail={response_preview or 'n/a'}"
                )

                if failed_counts[msg_id] >= MAX_RETRIES_PER_MESSAGE:
                    print(
                        f"[WATCHDOG] Skipping message {msg_id} after {failed_counts[msg_id]} failed attempts "
                        "to keep sync progressing."
                    )
                    save_last_synced_id(msg_id)
                    _post_watchdog_status(msg_id, latest_source_id)
                    _clear_failed_id(failed_counts, msg_id)
                    continue

                print("[WATCHDOG] Stopping this cycle to avoid log flood; will retry from last successful ID.")
                break
            except Exception as e:
                print(f"[WATCHDOG] Sync error on message {msg_id}: {e}")
                print("[WATCHDOG] Stopping this cycle to avoid log flood; will retry from last successful ID.")
                break
    
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
