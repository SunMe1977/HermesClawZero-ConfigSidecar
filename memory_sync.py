import time
import pathlib
import requests
import os
import logging
import shutil
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("hermesclaw.sync")

# Watch both standard sync and the new inbox
SYNC_FOLDERS = [pathlib.Path("sync"), pathlib.Path("inbox")]
ARCHIVE_FOLDER = pathlib.Path("archive")
API_URL = os.getenv("API_URL", "http://localhost:8010") + "/capture"
API_KEY = os.getenv("API_KEY", "YOUR_API_KEY_HERE")
PID_FILE = pathlib.Path("memory_sync.pid")

# Polling interval (seconds) between folder scans.
POLL_INTERVAL_SECONDS = float(os.getenv("SYNC_POLL_INTERVAL_SECONDS", "2"))
# How long (seconds) a file's size must stay stable before we treat it as fully written.
FILE_STABILITY_SECONDS = float(os.getenv("SYNC_FILE_STABILITY_SECONDS", "1.0"))
# Extensions eligible for ingestion.
ALLOWED_SUFFIXES = {".txt", ".md", ".log", ".json", ".html"}

# Liveness state read by the API /healthz endpoint.
LIVENESS = {
    "last_success_ts": None,
    "last_error_ts": None,
    "last_error": None,
    "processed_count": 0,
}


def write_pid():
    PID_FILE.write_text(str(os.getpid()))


def _file_size(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def _is_file_stable(path: pathlib.Path) -> bool:
    """Return True when the file size has not changed over FILE_STABILITY_SECONDS."""
    first_size = _file_size(path)
    if first_size < 0:
        return False
    time.sleep(FILE_STABILITY_SECONDS)
    return _file_size(path) == first_size


def import_file(path: pathlib.Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.error("Failed to read %s: %s", path.name, e)
        LIVENESS["last_error_ts"] = int(time.time())
        LIVENESS["last_error"] = f"read {path.name}: {e}"
        return

    resp = None
    try:
        resp = requests.post(API_URL, params={"key": API_KEY}, json={"text": text}, timeout=30)
        if resp.status_code != 200:
            logger.error("Error %s for %s: %s", resp.status_code, path.name, resp.text)
            LIVENESS["last_error_ts"] = int(time.time())
            LIVENESS["last_error"] = f"http {resp.status_code} {path.name}"
            return
        data = resp.json()
        logger.info("%s: %s", path.name, data)
    except Exception as e:
        logger.exception("Exception sending %s: %s", path.name, e)
        LIVENESS["last_error_ts"] = int(time.time())
        LIVENESS["last_error"] = f"send {path.name}: {e}"
        return

    # Archiving successful files
    try:
        shutil.move(str(path), str(ARCHIVE_FOLDER / path.name))
        logger.info("%s archived.", path.name)
        LIVENESS["last_success_ts"] = int(time.time())
        LIVENESS["processed_count"] = int(LIVENESS.get("processed_count") or 0) + 1
        LIVENESS["last_error"] = None
    except Exception as e:
        logger.exception("Failed to archive %s: %s", path.name, e)
        LIVENESS["last_error_ts"] = int(time.time())
        LIVENESS["last_error"] = f"archive {path.name}: {e}"


def run_sync():
    logger.info("Auto-Sync started, watching %s", ", ".join(str(f) for f in SYNC_FOLDERS))
    for folder in SYNC_FOLDERS:
        folder.mkdir(exist_ok=True)
    ARCHIVE_FOLDER.mkdir(exist_ok=True)

    write_pid()

    while True:
        for folder in SYNC_FOLDERS:
            for file in folder.iterdir():
                if not file.is_file():
                    continue
                if file.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                if not _is_file_stable(file):
                    logger.debug("Skipping %s, still being written.", file.name)
                    continue
                import_file(file)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_sync()
