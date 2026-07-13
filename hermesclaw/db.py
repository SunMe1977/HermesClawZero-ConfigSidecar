"""Database connection pool and schema management."""

import os
import threading
import logging
from typing import Any
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


def connect_db() -> Any:
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

            # ── Engraphis-inspired Ebbinghaus + bi-temporal columns ──
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS stability REAL NOT NULL DEFAULT 1.0")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS last_access TIMESTAMP")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS valid_to TIMESTAMP")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS superseded_by INT")

            # ── content_hash for idempotent imports / dedup ──
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS content_hash TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_content_hash ON pages(content_hash)")

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

            # ── Knowledge Graph (entities + relationships) ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                  id SERIAL PRIMARY KEY,
                  name TEXT NOT NULL,
                  entity_type TEXT NOT NULL DEFAULT 'unknown',
                  canonical_name TEXT,
                  metadata JSONB DEFAULT '{}',
                  first_seen TIMESTAMP DEFAULT NOW(),
                  last_seen TIMESTAMP DEFAULT NOW(),
                  frequency INT DEFAULT 1
                )
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS entity_mentions (
                  id SERIAL PRIMARY KEY,
                  entity_id INT REFERENCES entities(id) ON DELETE CASCADE,
                  page_id INT REFERENCES pages(id) ON DELETE CASCADE,
                  context TEXT,
                  mentioned_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_em_entity ON entity_mentions(entity_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_em_page ON entity_mentions(page_id)")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                  id SERIAL PRIMARY KEY,
                  source_entity_id INT REFERENCES entities(id) ON DELETE CASCADE,
                  target_entity_id INT REFERENCES entities(id) ON DELETE CASCADE,
                  relation_type TEXT NOT NULL DEFAULT 'related_to',
                  weight REAL DEFAULT 1.0,
                  metadata JSONB DEFAULT '{}',
                  first_seen TIMESTAMP DEFAULT NOW(),
                  last_seen TIMESTAMP DEFAULT NOW(),
                  frequency INT DEFAULT 1,
                  UNIQUE(source_entity_id, target_entity_id, relation_type)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_entity_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_entity_id)")

            # ── Memory versions (history tracking) ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_versions (
                  id SERIAL PRIMARY KEY,
                  page_id INT REFERENCES pages(id) ON DELETE CASCADE,
                  content TEXT NOT NULL,
                  memory_type TEXT,
                  importance REAL,
                  confidence REAL,
                  version INT NOT NULL DEFAULT 1,
                  change_reason TEXT DEFAULT 'capture',
                  created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mv_page ON memory_versions(page_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mv_version ON memory_versions(page_id, version)")

            # ── Memory tiers (hot/warm/cold) ──
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS memory_tier TEXT NOT NULL DEFAULT 'standard'")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS summary_text TEXT")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS compressed_content TEXT")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS parent_id INT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_tier ON pages(memory_tier)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_parent ON pages(parent_id)")

            # ── pgvector HNSW index for fast ANN search ──
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops) WITH (m=32, ef_construction=400)")
                logger.info("[SCHEMA] HNSW index created/verified (m=32, ef_construction=400)")
            except Exception:
                # HNSW requires pgvector >= 0.5; fall back to IVFFlat
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_ivfflat ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)")
                    logger.info("[SCHEMA] IVFFlat index created/verified (HNSW not available)")
                except Exception:
                    logger.warning("[SCHEMA] Could not create vector index — pgvector may need upgrade")

            conn.commit()

    # ── 10K-scale covering indexes ──
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_dash ON pages(scope_id, is_archived, created_at DESC) WHERE is_archived = FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_created ON pages(created_at DESC) WHERE is_archived = FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_type ON pages(memory_type, is_archived) WHERE is_archived = FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_tier_scope ON pages(memory_tier, scope_id) WHERE is_archived = FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_decay ON pages(last_used, confidence, is_archived) WHERE is_archived = FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_tenant ON pages(chat_id, scope_id, is_archived) WHERE is_archived = FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_freq ON pages(frequency DESC) WHERE is_archived = FALSE")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_em_entity_page ON entity_mentions(entity_id, page_id)")
            # ── 10K-scale: Nudge composite index ──
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_nudge ON pages(importance DESC, frequency DESC, last_used DESC) WHERE is_archived = FALSE")
            # ── 2M-scale: Materialized views for dashboard ──
            _ensure_2m_materialized_views(cur)
            conn.commit()
            logger.info("[SCHEMA] 2M-scale indexes + materialized views created")

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


def _ensure_2m_materialized_views(cur) -> None:
    """Create 2M-scale materialized views for dashboard performance."""
    cur.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS pages_stats_mv AS
        SELECT
            COALESCE(COUNT(*), 0) AS total,
            COALESCE(COUNT(*) FILTER (WHERE is_archived = FALSE), 0) AS active,
            COALESCE(COUNT(*) FILTER (WHERE is_archived = TRUE), 0) AS archived,
            COALESCE(COUNT(*) FILTER (WHERE memory_tier = 'hot'), 0) AS hot,
            COALESCE(COUNT(*) FILTER (WHERE memory_tier = 'warm'), 0) AS warm,
            COALESCE(COUNT(*) FILTER (WHERE memory_tier = 'standard'), 0) AS standard,
            COALESCE(COUNT(*) FILTER (WHERE memory_tier = 'cold'), 0) AS cold,
            COALESCE(COUNT(*) FILTER (WHERE confidence >= 0.7), 0) AS high_conf,
            COALESCE(COUNT(*) FILTER (WHERE confidence >= 0.4 AND confidence < 0.7), 0) AS med_conf,
            COALESCE(COUNT(*) FILTER (WHERE confidence < 0.4), 0) AS low_conf,
            COALESCE(AVG(importance)::numeric(5,3), 0) AS avg_importance,
            COALESCE(AVG(confidence)::numeric(5,3), 0) AS avg_confidence,
            NOW() AS computed_at
        FROM pages
    """)
    cur.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS pages_scope_stats_mv AS
        SELECT
            scope_id, COUNT(*) AS total,
            COUNT(*) FILTER (WHERE is_archived = FALSE) AS active
        FROM pages WHERE scope_id IS NOT NULL
        GROUP BY scope_id
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pss_mv_scope ON pages_scope_stats_mv(scope_id)")


def refresh_materialized_views() -> dict:
    """Refresh materialized views (called by optimizer). Returns row counts."""
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY pages_stats_mv")
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY pages_scope_stats_mv")
            conn.commit()
            cur.execute("SELECT total, active, hot, warm FROM pages_stats_mv")
            row = cur.fetchone()
    return {
        "total": row[0] if row else 0,
        "active": row[1] if row else 0,
        "views_refreshed": True,
    }
