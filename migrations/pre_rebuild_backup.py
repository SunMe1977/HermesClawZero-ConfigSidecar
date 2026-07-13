#!/usr/bin/env python3
"""
Pre-Rebuild Backup — sichere alle Pages vor einem Container-Neubau.

Wird VOR docker compose down aufgerufen. Exportiert alle Pages + Embeddings
als JSON nach /var/lib/postgresql/backups/exports/ (persistentes Volume).

Nach dem Rebuild findet der Startup-Recovery die Datei und stellt sie wieder her.
"""
import json, os, sys, gzip
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path("/var/lib/postgresql/backups/exports")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def do_backup(db_host="localhost", db_port=5432, db_name="gbrain", db_user="postgres", db_pass=""):
    import psycopg2
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_pass,
    )
    with conn.cursor() as cur:
        # Pages zählen
        cur.execute("SELECT COUNT(*) FROM pages")
        count = cur.fetchone()[0]
        print(f"[PRE-BACKUP] Pages in DB: {count}")

        if count == 0:
            print("[PRE-BACKUP] Nothing to backup — DB is empty")
            return {"status": "skipped", "reason": "empty", "pages": 0}

        # Alle Pages exportieren
        cur.execute("""
            SELECT id, content, memory_type, importance, confidence, frequency,
                   source, scope_id, created_at, updated_at, archived_at,
                   metadata, tags, embed_model, sync_status, is_deleted
            FROM pages ORDER BY id
        """)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]

        # Embeddings separat (optional — werden beim Import neu generiert)
        try:
            cur.execute("SELECT page_id, embedding::text FROM embeddings ORDER BY page_id")
            embeddings = [{"page_id": r[0], "embedding_str": r[1]} for r in cur.fetchall()]
        except Exception:
            embeddings = []

        pages = [dict(zip(columns, row)) for row in rows]
        # Datumsfelder serialisierbar machen
        for p in pages:
            for k in ("created_at", "updated_at", "archived_at"):
                if p.get(k):
                    p[k] = p[k].isoformat()

        backup = {
            "version": 2,
            "exported_at": datetime.utcnow().isoformat(),
            "page_count": len(pages),
            "embedding_count": len(embeddings),
            "pages": pages,
            "embeddings": embeddings,
        }

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = BACKUP_DIR / f"pre_rebuild_{ts}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False)

        size_mb = path.stat().st_size / 1024 / 1024
        print(f"[PRE-BACKUP] Saved {len(pages)} pages + {len(embeddings)} embeddings → {path.name} ({size_mb:.1f} MB)")

        # Alte Backups aufräumen (älter als 7 Tage)
        cutoff = datetime.now().timestamp() - 7 * 86400
        for f in BACKUP_DIR.glob("pre_rebuild_*.json.gz"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                print(f"[PRE-BACKUP] Cleaned old backup: {f.name}")

    conn.close()
    return {"status": "ok", "pages": len(pages), "embeddings": len(embeddings), "path": str(path)}


def do_restore(db_host="localhost", db_port=5432, db_name="gbrain", db_user="postgres", db_pass=""):
    """Finde das neueste Pre-Rebuild Backup und stelle es wieder her."""
    backups = sorted(BACKUP_DIR.glob("pre_rebuild_*.json.gz"), reverse=True)
    if not backups:
        print("[POST-RESTORE] No pre-rebuild backup found — skipping restore")
        return {"status": "skipped", "reason": "no_backup_found"}

    latest = backups[0]
    print(f"[POST-RESTORE] Found backup: {latest.name}")

    import psycopg2
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_pass,
    )
    with conn.cursor() as cur:
        # Prüfen ob DB leer ist
        cur.execute("SELECT COUNT(*) FROM pages")
        current_count = cur.fetchone()[0]
        if current_count > 100:
            print(f"[POST-RESTORE] DB already has {current_count} pages — skipping restore")
            conn.close()
            return {"status": "skipped", "reason": "db_not_empty", "current_pages": current_count}

        # Backup laden
        with gzip.open(latest, "rt", encoding="utf-8") as f:
            backup = json.load(f)

        pages = backup["pages"]
        print(f"[POST-RESTORE] Restoring {len(pages)} pages from {latest.name}...")

        restored = 0
        errors = 0
        for p in pages:
            try:
                cur.execute("""
                    INSERT INTO pages (id, content, memory_type, importance, confidence, frequency,
                                       source, scope_id, created_at, updated_at,
                                       metadata, tags, embed_model, sync_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    p["id"], p["content"], p.get("memory_type", "conversation"),
                    p.get("importance", 0.5), p.get("confidence", 0.7), p.get("frequency", 1),
                    p.get("source"), p.get("scope_id"),
                    p.get("created_at"), p.get("updated_at"),
                    json.dumps(p.get("metadata") or {}),
                    json.dumps(p.get("tags") or []),
                    p.get("embed_model"), p.get("sync_status", "active"),
                ))
                restored += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"[POST-RESTORE] Error on page {p.get('id')}: {e}")

        conn.commit()
        print(f"[POST-RESTORE] Restored {restored} pages ({errors} errors)")
        print(f"[POST-RESTORE] Embeddings will be regenerated automatically by the worker")

    conn.close()

    # Backup als verarbeitet markieren
    latest.rename(latest.with_suffix(".json.gz.restored"))
    return {"status": "ok", "restored": restored, "errors": errors, "from": latest.name}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pre-Rebuild Backup / Post-Rebuild Restore")
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--db-host", default=os.environ.get("DB_HOST", "localhost"))
    parser.add_argument("--db-port", default=os.environ.get("DB_PORT", "5432"))
    parser.add_argument("--db-name", default=os.environ.get("DB_NAME", "gbrain"))
    parser.add_argument("--db-user", default=os.environ.get("DB_USER", "postgres"))
    parser.add_argument("--db-pass", default=os.environ.get("DB_PASSWORD", ""))
    args = parser.parse_args()

    if args.action == "backup":
        result = do_backup(args.db_host, args.db_port, args.db_name, args.db_user, args.db_pass)
    else:
        result = do_restore(args.db_host, args.db_port, args.db_name, args.db_user, args.db_pass)

    print(json.dumps(result))
    sys.exit(0 if result.get("status") == "ok" or result.get("status") == "skipped" else 1)
