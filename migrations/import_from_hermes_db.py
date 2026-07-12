#!/usr/bin/env python3
"""
Migration: Import memories from Hermes state.db (SQLite) into Sidecar pages (PostgreSQL).  

This script reads Hermes' SQLite state.db (which stores session data including
captures) and imports relevant records into the Sidecar's PostgreSQL 'pages' table.
It handles deduplication by content hash to prevent duplicate imports across
multiple runs.

Usage:
    # Run inside the API container (has PostgreSQL access):
    python migrations/import_from_hermes_db.py
    
    # Or specify a custom Hermes state.db path:
    python migrations/import_from_hermes_db.py --hermes-db /root/.hermes/state.db

    # Dry-run (show what would be imported without writing):
    python migrations/import_from_hermes_db.py --dry-run

Requirements:
    - psycopg (installed in the API container via requirements.txt)
    - Access to Hermes state.db (mounted at /hermes_state/state.db in container,
      or /root/.hermes/state.db on the host)
"""

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_HERMES_DB = "/hermes_state/state.db"

DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "gbrain")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
SCOPE_ID = os.getenv("MIGRATE_SCOPE_ID", "hermes_import")
CHAT_ID = os.getenv("MIGRATE_CHAT_ID", "global")


# ---------------------------------------------------------------------------
# Hermes state.db reader
# ---------------------------------------------------------------------------
def get_hermes_captures(db_path: str) -> list[dict[str, Any]]:
    """Extract captures from Hermes' SQLite state.db."""
    if not os.path.isfile(db_path):
        logger.warning("Hermes state.db not found at: %s", db_path)
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Discover tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row["name"] for row in cur.fetchall()]
    logger.info("Hermes state.db tables: %s", tables)

    captures = []
    count = 0

    # Strategy 1: Look for a 'captures' or 'memories' table
    for tbl in ("captures", "memories", "memory_entries", "entries"):
        if tbl in tables:
            try:
                cur.execute(f"SELECT * FROM \"{tbl}\" ORDER BY rowid DESC LIMIT 10000")
                cols = [desc[0] for desc in cur.description]
                for row in cur.fetchall():
                    captures.append(dict(row))
                    count += 1
                logger.info("  Table '%s': %d records found", tbl, len(captures))
            except Exception as e:
                logger.debug("  Table '%s': %s", tbl, e)
            break
    else:
        logger.info("  No dedicated captures/memories table found.")

    # Strategy 2: Session-based captures in messages table
    if "messages" in tables:
        try:
            cur.execute(
                """
                SELECT m.id, m.role, m.content, m.timestamp as created_at, s.title as session_title
                FROM messages m
                LEFT JOIN sessions s ON m.session_id = s.id
                WHERE m.content IS NOT NULL 
                  AND LENGTH(m.content) > 10
                  AND m.role IN ('user', 'assistant')
                ORDER BY m.id DESC
                LIMIT 10000
                """
            )
            cols = [desc[0] for desc in cur.description]
            for row in cur.fetchall():
                row_dict = dict(row)
                row_dict["_source_table"] = "messages"
                captures.append(row_dict)
                count += 1
            logger.info("  Table 'messages': %d records found", count)
        except Exception as e:
            logger.debug("  Table 'messages': %s", e)

    conn.close()
    logger.info("Total captures extracted: %d", len(captures))
    return captures


def make_content_hash(text: str) -> str:
    """SHA-256 content hash for deduplication."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_to_pages_row(capture: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Hermes capture dict into a pages table row."""
    text = capture.get("content") or capture.get("text") or ""
    if not text or len(text.strip()) < 5:
        return None

    text = text.strip()[:10000]  # Truncate to max content length

    # Parse created_at
    created_raw = capture.get("created_at") or capture.get("timestamp") or datetime.now(timezone.utc)
    if isinstance(created_raw, str):
        try:
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.now(timezone.utc)
    elif isinstance(created_raw, (int, float)):
        created_at = datetime.fromtimestamp(created_raw, tz=timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    # Determine memory type from role
    role = capture.get("role", "assistant")
    if role == "user":
        memory_type = "user_query"
    elif role == "assistant":
        memory_type = "conversation"
    else:
        memory_type = "capture"

    # Source
    source = capture.get("_source_table", "hermes_import")

    return {
        "content": text,
        "content_hash": make_content_hash(text),
        "memory_type": memory_type,
        "source": source,
        "scope_id": SCOPE_ID,
        "chat_id": CHAT_ID,
        "created_at": created_at,
        "updated_at": created_at,
        "last_used": created_at,
        "importance": 0.5,
        "confidence": 0.7,
        "frequency": 1,
        "sentiment": 0.0,
    }


# ---------------------------------------------------------------------------
# PostgreSQL writer
# ---------------------------------------------------------------------------
def get_existing_hashes() -> set[str]:
    """Get all content hashes already in the pages table."""
    try:
        import psycopg
        conn = psycopg.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute("SELECT content_hash FROM pages WHERE content_hash IS NOT NULL")
        hashes = {row[0] for row in cur.fetchall()}
        conn.close()
        return hashes
    except Exception as e:
        logger.warning("Could not query existing hashes: %s", e)
        return set()


def ensure_content_hash_column() -> None:
    """Add content_hash column to pages if not exists."""
    try:
        import psycopg
        conn = psycopg.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS content_hash TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_content_hash ON pages(content_hash)")
        conn.commit()
        conn.close()
        logger.info("content_hash column ensured on pages table")
    except Exception as e:
        logger.warning("Could not add content_hash column: %s", e)


def import_rows(rows: list[dict[str, Any]], dry_run: bool = False) -> int:
    """Import rows into PostgreSQL pages table."""
    if not rows:
        logger.info("No rows to import.")
        return 0

    try:
        import psycopg
    except ImportError:
        logger.error("psycopg is not installed. Run: pip install psycopg[binary]")
        return 0

    if dry_run:
        logger.info("=== DRY RUN - Would import %d memories ===", len(rows))
        for i, row in enumerate(rows[:5]):
            preview = row["content"][:80]
            logger.info("  %d. [%s] %s...", i + 1, row["memory_type"], preview)
        if len(rows) > 5:
            logger.info("  ... and %d more", len(rows) - 5)
        return len(rows)

    conn = psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )
    imported = 0
    skipped = 0

    INSERT_SQL = """
        INSERT INTO pages (
            content, content_hash, memory_type, source, scope_id, chat_id,
            created_at, updated_at, last_used, importance, confidence,
            frequency, sentiment
        ) VALUES (
            %(content)s, %(content_hash)s, %(memory_type)s, %(source)s,
            %(scope_id)s, %(chat_id)s, %(created_at)s, %(updated_at)s,
            %(last_used)s, %(importance)s, %(confidence)s,
            %(frequency)s, %(sentiment)s
        )
        ON CONFLICT DO NOTHING
    """

    with conn.cursor() as cur:
        for row in rows:
            try:
                cur.execute(INSERT_SQL, row)
                if cur.rowcount and cur.rowcount > 0:
                    imported += 1
            except Exception as e:
                skipped += 1
                if skipped <= 3:
                    logger.debug("Skip row: %s", e)
                continue

    conn.commit()
    conn.close()

    logger.info("Imported: %d | Skipped: %d", imported, skipped)

    # Step 5: Generate embeddings for newly imported pages
    if imported > 0:
        _generate_missing_embeddings()

    return imported


def _get_embedding_provider() -> str | None:
    """Get the configured embedding provider."""
    return os.getenv("EMBEDDING_PROVIDER") or os.getenv("AI_PROVIDER") or "openrouter"


def _generate_missing_embeddings() -> int:
    """Generate embeddings for pages that don't have them yet."""
    try:
        import sys
        sys.path.insert(0, "/app/repo")
        from hermesclaw.embeddings import generate_embedding
    except ImportError:
        logger.warning(
            "Cannot import hermesclaw.embeddings — skipping embedding generation. "
            "They will be generated on next API restart via ensure_phase1_schema."
        )
        return 0

    try:
        import psycopg
        conn = psycopg.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )

        # Find pages without embeddings
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.content
            FROM pages p
            LEFT JOIN embeddings e ON e.page_id = p.id
            WHERE e.id IS NULL
            ORDER BY p.id
        """)
        missing = cur.fetchall()
        conn.close()

        if not missing:
            logger.info("All pages already have embeddings.")
            return 0

        logger.info(
            "Generating embeddings for %d pages (provider: %s) ...",
            len(missing), _get_embedding_provider(),
        )

        conn2 = psycopg.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        generated = 0
        errors = 0

        with conn2.cursor() as cur2:
            for page_id, content in missing:
                if not content or len(content.strip()) < 5:
                    continue
                try:
                    emb = generate_embedding(content.strip()[:8000])
                    if emb and len(emb) > 0:
                        cur2.execute(
                            "INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)",
                            (page_id, str(emb)),
                        )
                        generated += 1
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        logger.debug("  Embedding error for page %d: %s", page_id, e)

                if generated % 100 == 0 and generated > 0:
                    conn2.commit()
                    logger.info("  ... %d embeddings generated", generated)

        conn2.commit()
        conn2.close()

        logger.info("Embeddings: %d generated, %d errors", generated, errors)
        return generated

    except Exception as e:
        logger.warning("Embedding generation failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Hermes state.db memories into Sidecar PostgreSQL"
    )
    parser.add_argument(
        "--hermes-db", default=DEFAULT_HERMES_DB,
        help=f"Path to Hermes state.db (default: {DEFAULT_HERMES_DB})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be imported without writing",
    )
    args = parser.parse_args()

    logger.info("Migration: Hermes state.db → Sidecar pages")
    logger.info("Hermes DB: %s", args.hermes_db)
    logger.info("PostgreSQL: %s@%s:%d/%s", DB_USER, DB_HOST, DB_PORT, DB_NAME)
    logger.info("Dry run: %s", args.dry_run)

    # Step 1: Read Hermes captures
    captures = get_hermes_captures(args.hermes_db)

    # Step 2: Convert to pages rows
    rows = []
    for cap in captures:
        row = content_to_pages_row(cap)
        if row:
            rows.append(row)

    logger.info("Converted to %d pages rows", len(rows))

    # Step 3: Deduplicate
    ensure_content_hash_column()
    existing = get_existing_hashes()
    rows = [r for r in rows if r["content_hash"] not in existing]
    logger.info("After dedup: %d new rows", len(rows))

    # Step 4: Import
    import_rows(rows, dry_run=args.dry_run)

    # Step 5: Summary
    if not args.dry_run:
        try:
            import psycopg
            conn = psycopg.connect(
                host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                user=DB_USER, password=DB_PASSWORD,
            )
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM pages")
            total = cur.fetchone()[0]
            conn.close()
            logger.info("Total memories in pages table now: %d", total)
        except Exception as e:
            logger.warning("Could not get final count: %s", e)


if __name__ == "__main__":
    main()
