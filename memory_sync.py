import time
import pathlib
import requests
import os
import psutil
import shutil
from dotenv import load_dotenv

load_dotenv()

# Watch both standard sync and the new inbox
SYNC_FOLDERS = [pathlib.Path("sync"), pathlib.Path("inbox")]
ARCHIVE_FOLDER = pathlib.Path("archive")
API_URL = os.getenv("API_URL", "http://localhost:8010") + "/capture"
API_KEY = os.getenv("API_KEY", "YOUR_API_KEY_HERE")
PID_FILE = pathlib.Path("memory_sync.pid")

def write_pid():
    PID_FILE.write_text(str(os.getpid()))

def import_file(path: pathlib.Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[SYNC] Fehler beim Lesen von {path.name}: {e}")
        return

    try:
        resp = requests.post(API_URL, params={"key": API_KEY}, json={"text": text})
        if resp.status_code != 200:
            print(f"[SYNC] Error {resp.status_code}: {resp.text}")
        else:
            data = resp.json()
            print(f"[SYNC] {path.name}: {data}")

            # Archiving successful files
            shutil.move(str(path), str(ARCHIVE_FOLDER / path.name))
            print(f"[SYNC] {path.name} archiviert.")
    except Exception as e:
        print(f"[SYNC] Exception beim Senden von {path.name}: {e}")
        print(f"DEBUG: Response was: {resp.text if 'resp' in locals() else 'No response'}")

def run_sync():
    print("[SYNC] Auto-Sync gestartet... überwache ./sync/ und ./inbox/")
    for folder in SYNC_FOLDERS:
        folder.mkdir(exist_ok=True)
    ARCHIVE_FOLDER.mkdir(exist_ok=True)

    write_pid()

    while True:
        for folder in SYNC_FOLDERS:
            for file in folder.iterdir():
                if file.is_file() and file.suffix.lower() in [
                    ".txt", ".md", ".log", ".json", ".html"
                ]:
                    import_file(file)

        time.sleep(2)

if __name__ == "__main__":
    run_sync()
