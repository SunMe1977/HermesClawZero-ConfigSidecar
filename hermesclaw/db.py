"""Database connection pool and schema management."""

import os
import threading
import logging
from psycopg_pool import ConnectionPool
from hermesclaw.config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    ALLOW_EMBEDDING_SCHEMA_RESET,
)

logger = logging.getLogger("hermesclaw.db")

_db_pool: ConnectionPool | None = None
_db_pool_lock = threading.Lock()
_shutdown_event = threading.Event()


def get_db_pool() -> ConnectionPool:
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    with _db_pool_lock:
        if _db_pool is None:
            conninfo = (
                f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
                f"user={DB_USER} password={DB_PASSWORD}"
            )
            _db_pool = ConnectionPool(
                conninfo=conninfo,
                min_size=int(os.getenv("DB_POOL_MIN", "2")),
                max_size=int(os.getenv("DB_POOL_MAX", "10")),
                timeout=float(os.getenv("DB_POOL_TIMEOUT", "30")),
            )
            logger.info(
                "DB connection pool created (min=%s max=%s)",
                os.getenv("DB_POOL_MIN", "2"),
                os.getenv("DB_POOL_MAX", "10"),
            )
    return _db_pool


def close_db_pool() -> None:
    global _db_pool
    if _db_pool is not None:
        _db_pool.close()
        _db_pool = None
        logger.info("DB connection pool closed.")


def connect_db():
    return get_db_pool().connection()


def embedding_to_pgvector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


def ensure_embeddings_schema(expected_dim: int) -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'embeddings'
                )
                """
            )
            exists = bool(cur.fetchone()[0])

            if not exists:
                cur.execute(
                    f"""
                    CREATE TABLE embeddings (
                      id SERIAL PRIMARY KEY,
                      page_id INT REFERENCES pages(id) ON DELETE CASCADE,
                      embedding vector({expected_dim}),
                      created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                conn.commit()
                return

            cur.execute(
                """
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = 'public'
                  AND c.relname = 'embeddings'
                  AND a.attname = 'embedding'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                LIMIT 1
                """
            )
            row = cur.fetchone()
            current_type = row[0] if row else ""
            expected_type = f"vector({expected_dim})"

            if current_type != expected_type:
                message = (
                    "[SCHEMA] embeddings.embedding type mismatch "
                    f"({current_type or 'unknown'} -> {expected_type}). "
                    "Automatic destructive reset is disabled. "
                    "Run rebuild_embeddings.py or set ALLOW_EMBEDDING_SCHEMA_RESET=true to force reset."
                )
                if not ALLOW_EMBEDDING_SCHEMA_RESET:
                    logger.error(message)
                    raise RuntimeError(message)

                logger.warning(
                    "%s Proceeding with forced reset due to ALLOW_EMBEDDING_SCHEMA_RESET=true.", message
                )
                cur.execute("DROP TABLE IF EXISTS embeddings")
                cur.execute(
                    f"""
                    CREATE TABLE embeddings (
                      id SERIAL PRIMARY KEY,
                      page_id INT REFERENCES pages(id) ON DELETE CASCADE,
                      embedding vector({expected_dim}),
                      created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
            conn.commit()


def ensure_phase1_schema() -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS memory_type TEXT NOT NULL DEFAULT 'conversation'")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS importance REAL NOT NULL DEFAULT 0.5")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS confidence REAL NOT NULL DEFAULT 0.8")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS frequency INT NOT NULL DEFAULT 1")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS sentiment REAL NOT NULL DEFAULT 0.0")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'capture'")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS ttl_days INT")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS last_used TIMESTAMP DEFAULT NOW()")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS last_retrieved TIMESTAMP")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS scope_id TEXT")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS chat_id TEXT NOT NULL DEFAULT 'global'")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pages_archive (
                  archive_id SERIAL PRIMARY KEY,
                  page_id INT,
                  content TEXT NOT NULL,
                  memory_type TEXT,
                  importance REAL,
                  confidence REAL,
                  frequency INT,
                  sentiment REAL,
                  source TEXT,
                  ttl_days INT,
                  created_at TIMESTAMP,
                  updated_at TIMESTAMP,
                  last_used TIMESTAMP,
                  last_retrieved TIMESTAMP,
                  archived_at TIMESTAMP DEFAULT NOW(),
                  archive_reason TEXT DEFAULT 'decay'
                )
                """
            )

            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_memory_type ON pages(memory_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_last_used ON pages(last_used DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_archived ON pages(is_archived)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_pages_fts_content ON pages USING GIN (to_tsvector('english', content))"
            )
            cur.execute("ALTER TABLE pages_archive ADD COLUMN IF NOT EXISTS archive_batch_id TEXT")
            cur.execute("ALTER TABLE pages_archive ADD COLUMN IF NOT EXISTS scope_id TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_archive_batch ON pages_archive(archive_batch_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_scope_id ON pages(scope_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_chat_id ON pages(chat_id)")
            conn.commit()

    from hermesclaw.embeddings import infer_embedding_dimension
    ensure_embeddings_schema(infer_embedding_dimension())


def cleanup_orphaned_embeddings() -> int:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM embeddings
                WHERE page_id NOT IN (SELECT id FROM pages)
                """
            )
            deleted_count = int(cur.rowcount or 0)
            conn.commit()
    return deleted_count
