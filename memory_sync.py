import time
import pathlib
import requests
import os
import psutil

SYNC_FOLDER = pathlib.Path("sync")
API_URL = "http://localhost:8000/capture"
PID_FILE = pathlib.Path("memory_sync.pid")

imported_files = set()

def write_pid():
    PID_FILE.write_text(str(os.getpid()))

def pid_running():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text())
            return psutil.pid_exists(pid)
        except:
            return False
    return False

def import_file(path: pathlib.Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[SYNC] Fehler beim Lesen von {path.name}: {e}")
        return

    resp = requests.post(API_URL, params={"text": text})
    data = resp.json()

    print(f"[SYNC] {path.name}: {data}")

def run_sync():
    print("[SYNC] Auto-Sync gestartet... überwache ./sync/")
    SYNC_FOLDER.mkdir(exist_ok=True)

    write_pid()

    while True:
        for file in SYNC_FOLDER.iterdir():
            if file.is_file() and file.suffix.lower() in [
                ".txt", ".md", ".log", ".json", ".html"
            ]:
                if file not in imported_files:
                    imported_files.add(file)
                    import_file(file)

        time.sleep(2)

# Wird von main.py gestartet