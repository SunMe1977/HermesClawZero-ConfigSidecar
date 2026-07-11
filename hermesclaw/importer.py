"""
First-run Hermes DB importer.
Detects existing Hermes state.db, imports sessions + messages as Sidecar memories.
Uses file-based marker (import_markers.json) so no extra DB dependency.
Safe to re-run — memory.py capture deduplicates by content anyway.
"""
import os, sys, json, time, logging
from datetime import datetime

logger = logging.getLogger("hermesclaw.importer")

MARKER_FILE = os.path.join(os.path.dirname(__file__), "..", "import_markers.json")

# ── Target: Sidecar API (same as memory.py uses) ──
MEM_PUBLIC_URL = os.getenv("MEM_PUBLIC_URL", "http://localhost:8010")
API_KEY = os.getenv("API_KEY", "")

# ── Source: Hermes state.db ──
HERMES_DB_PATH = os.getenv("HERMES_DB_PATH", "")
HERMES_DB_CANDIDATES = [
    HERMES_DB_PATH,
    os.path.expanduser("~/AppData/Local/hermes/state.db"),
    os.path.expanduser("~/.local/share/hermes/state.db"),
    "/app/data/state.db",
]


def _find_hermes_db() -> str | None:
    for p in HERMES_DB_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def _load_markers() -> set[str]:
    try:
        with open(MARKER_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_marker(sid: str, markers: set[str]):
    markers.add(sid)
    with open(MARKER_FILE, "w") as f:
        json.dump(sorted(markers), f, indent=2)


def _capture(text: str, scope_id: str | None = None):
    """Write to Sidecar via REST API."""
    import urllib.request, urllib.error

    payload = json.dumps({"text": text, "scope_id": scope_id or "import:hermes"}).encode()
    req = urllib.request.Request(
        f"{MEM_PUBLIC_URL}/capture",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.warning("API capture HTTP %s: %s", e.code, body[:200])
        return {"status": "error", "error": body}
    except Exception as ex:
        logger.warning("API capture failed: %s", ex)
        return {"status": "error", "error": str(ex)}


def import_hermes_sessions(dry_run: bool = False) -> dict:
    """Import all un-imported sessions from Hermes state.db into Sidecar memory.

    Returns stats dict. Idempotent: tracks already-imported session IDs in
    import_markers.json so subsequent runs skip them.
    """
    db_path = _find_hermes_db()
    if not db_path:
        return {"status": "skipped", "reason": "Hermes state.db not found"}

    if not API_KEY:
        return {"status": "skipped", "reason": "API_KEY not set — cannot write to Sidecar"}

    import sqlite3

    try:
        source = sqlite3.connect(db_path)
        source.row_factory = sqlite3.Row
    except Exception as ex:
        return {"status": "error", "error": f"Cannot open Hermes DB: {ex}"}

    markers = _load_markers()
    stats = {"sessions_found": 0, "sessions_imported": 0, "messages_imported": 0, "errors": []}

    try:
        cur = source.execute("SELECT * FROM sessions ORDER BY started_at ASC")
        sessions = [dict(r) for r in cur.fetchall()]
        stats["sessions_found"] = len(sessions)

        for sess in sessions:
            sid = sess["id"]
            if sid in markers:
                continue

            msg_count = sess.get("message_count", 0)
            title = sess.get("title") or f"Session {sid}"
            model = sess.get("model", "unknown")
            started = datetime.fromtimestamp(sess.get("started_at", 0)).strftime("%Y-%m-%d %H:%M")
            input_tok = sess.get("input_tokens", 0) or 0
            output_tok = sess.get("output_tokens", 0) or 0

            summary = (
                f"Hermes session: {title} | "
                f"Started: {started} | "
                f"Model: {model} | "
                f"Messages: {msg_count} | "
                f"Tokens: {input_tok + output_tok} total"
            )

            if not dry_run:
                result = _capture(summary, scope_id=f"session:{sid}")
                if result.get("status") != "ok":
                    stats["errors"].append(f"Session {sid} summary: {result.get('error','?')}")
                    # Continue anyway — try messages

                # Capture every 5th user message for context
                msg_cur = source.execute(
                    "SELECT id, content, timestamp FROM messages "
                    "WHERE session_id=? AND role='user' AND content IS NOT NULL AND content != '' "
                    "ORDER BY id ASC",
                    (sid,),
                )
                msgs = [dict(m) for m in msg_cur.fetchall()]
                for i, msg in enumerate(msgs):
                    if i % 5 != 0:
                        continue
                    content = (msg.get("content") or "").strip()
                    if not content or len(content) < 20:
                        continue
                    preview = content[:300]
                    ts = datetime.fromtimestamp(msg.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M")
                    memory_text = f"[{ts}] User asked/discussed: {preview}"
                    _capture(memory_text, scope_id=f"session:{sid}")
                    stats["messages_imported"] += 1

                _save_marker(sid, markers)
                stats["sessions_imported"] += 1
                logger.info("Imported session %s: %s (%d msgs)", sid, title, msg_count)

    except Exception as ex:
        stats["errors"].append(str(ex))
    finally:
        source.close()

    stats["status"] = "ok"
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s [%(levelname)s] %(message)s")
    result = import_hermes_sessions(dry_run="--dry-run" in sys.argv)
    print(json.dumps(result, indent=2, default=str))
    if result.get("errors"):
        sys.exit(1)
