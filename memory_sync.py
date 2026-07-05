import time
import pathlib
import requests
import os
import psutil

SYNC_FOLDER = pathlib.Path("sync")
API_URL = os.getenv("MEM_PUBLIC_URL", "https://openclawmemwin.postarmory.com") + "/capture"
API_KEY = os.getenv("API_KEY", "YOUR_API_KEY_HERE")
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

    resp = requests.post(API_URL, params={"key": API_KEY}, json={"text": text})
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

if __name__ == "__main__":
    run_sync()
