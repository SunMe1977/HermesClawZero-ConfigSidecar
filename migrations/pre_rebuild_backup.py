#!/usr/bin/env python3
"""Pre-Rebuild Backup — export all pages before container rebuild.

Called BEFORE docker rebuild via UPDATE_RESTART_COMMAND.
Exports pages as JSON to a persistent volume that survives rebuilds.
After rebuild, startup_event checks for backups and restores if DB is empty.

Usage:
    python migrations/pre_rebuild_backup.py backup
    python migrations/pre_rebuild_backup.py restore
"""
import json, os, sys, gzip, logging
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path("/var/lib/postgresql/backups/exports")

# Reuse the project's DB helpers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hermesclaw.db import connect_db, embedding_to_pgvector_literal
from hermesclaw.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

logger = logging.getLogger("pre_rebuild_backup")


def do_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pages WHERE is_archived = FALSE")
            count = cur.fetchone()[0]
            print(f"[PRE-BACKUP] Active pages: {count}")

            if count == 0:
                print("[PRE-BACKUP] Nothing to backup")
                return {"status": "skipped", "reason": "empty", "pages": 0}

            # Export active pages (all schema columns we have)
            cur.execute("""
                SELECT id, content, memory_type, importance, confidence, frequency,
                       sentiment, source, scope_id, chat_id, memory_tier,
                       stability, ttl_days, content_hash, summary_text, compressed_content,
                       parent_id, valid_to, superseded_by,
                       created_at, updated_at, last_used, last_access, last_retrieved
                FROM pages
                WHERE is_archived = FALSE
                ORDER BY id
            """)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

            # Serialize datetimes
            def _serialize(val):
                if hasattr(val, 'isoformat'):
                    return val.isoformat()
                return val

            pages = []
            for row in rows:
                page = {}
                for i, col in enumerate(columns):
                    page[col] = _serialize(row[i])
                pages.append(page)

            backup = {
                "version": 3,
                "exported_at": datetime.utcnow().isoformat(),
                "page_count": len(pages),
                "pages": pages,
            }

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = BACKUP_DIR / f"pre_rebuild_{ts}.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(backup, f, ensure_ascii=False)

            size_mb = path.stat().st_size / 1024 / 1024
            print(f"[PRE-BACKUP] Saved {len(pages)} pages -> {path.name} ({size_mb:.1f} MB)")

            # Clean old backups (>7 days)
            cutoff = datetime.now().timestamp() - 7 * 86400
            for f in BACKUP_DIR.glob("pre_rebuild_*.json.gz"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    print(f"[PRE-BACKUP] Cleaned: {f.name}")

    return {"status": "ok", "pages": len(pages), "path": str(path)}


def do_restore():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted(BACKUP_DIR.glob("pre_rebuild_*.json.gz"), reverse=True)
    if not backups:
        print("[RECOVERY] No pre-rebuild backup found")
        return {"status": "skipped", "reason": "no_backup_found"}

    latest = backups[0]
    print(f"[RECOVERY] Found backup: {latest.name}")

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pages")
            current = cur.fetchone()[0]
            if current > 100:
                print(f"[RECOVERY] DB has {current} pages, skipping restore")
                return {"status": "skipped", "reason": "not_empty", "current": current}

            with gzip.open(latest, "rt", encoding="utf-8") as f:
                backup = json.load(f)

            pages = backup["pages"]
            print(f"[RECOVERY] Restoring {len(pages)} pages...")

            restored = 0
            errors = 0
            for p in pages:
                try:
                    cur.execute("""
                        INSERT INTO pages (
                            content, memory_type, importance, confidence, frequency,
                            sentiment, source, scope_id, chat_id, memory_tier,
                            stability, ttl_days, content_hash, summary_text, compressed_content,
                            parent_id, valid_to, superseded_by,
                            created_at, updated_at, last_used, last_access, last_retrieved
                        ) VALUES (
                            %(content)s, %(memory_type)s, %(importance)s, %(confidence)s,
                            %(frequency)s, %(sentiment)s, %(source)s, %(scope_id)s,
                            %(chat_id)s, %(memory_tier)s, %(stability)s, %(ttl_days)s,
                            %(content_hash)s, %(summary_text)s, %(compressed_content)s,
                            %(parent_id)s, %(valid_to)s, %(superseded_by)s,
                            %(created_at)s, %(updated_at)s, %(last_used)s, %(last_access)s,
                            %(last_retrieved)s
                        )
                    """, {
                        "content": p.get("content", ""),
                        "memory_type": p.get("memory_type", "conversation"),
                        "importance": p.get("importance", 0.5),
                        "confidence": p.get("confidence", 0.8),
                        "frequency": p.get("frequency", 1),
                        "sentiment": p.get("sentiment", 0.0),
                        "source": p.get("source", "backup_restore"),
                        "scope_id": p.get("scope_id"),
                        "chat_id": p.get("chat_id", "global"),
                        "memory_tier": p.get("memory_tier", "standard"),
                        "stability": p.get("stability", 1.0),
                        "ttl_days": p.get("ttl_days"),
                        "content_hash": p.get("content_hash"),
                        "summary_text": p.get("summary_text"),
                        "compressed_content": p.get("compressed_content"),
                        "parent_id": p.get("parent_id"),
                        "valid_to": p.get("valid_to"),
                        "superseded_by": p.get("superseded_by"),
                        "created_at": p.get("created_at"),
                        "updated_at": p.get("updated_at"),
                        "last_used": p.get("last_used"),
                        "last_access": p.get("last_access"),
                        "last_retrieved": p.get("last_retrieved"),
                    })
                    restored += 1
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        print(f"[RECOVERY] Error on page {p.get('id','?')}: {e}")

            conn.commit()
            print(f"[RECOVERY] Restored {restored} pages ({errors} errors)")
            print(f"[RECOVERY] Embeddings will regenerate via background worker")

    latest.rename(latest.with_suffix(".json.gz.restored"))
    return {"status": "ok", "restored": restored, "errors": errors, "from": latest.name}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    args = parser.parse_args()

    if args.action == "backup":
        result = do_backup()
    else:
        result = do_restore()

    print(json.dumps(result))
    sys.exit(0 if result.get("status") in ("ok", "skipped") else 1)
