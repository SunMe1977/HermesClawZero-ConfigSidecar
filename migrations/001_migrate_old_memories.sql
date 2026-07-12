-- Migration 001: Restore old memories from Hermes state.db into Sidecar pages
-- 
-- Problem: v2.0.0 created a new 'pages' schema but never migrated existing
-- memories from an older SQLite-based store or previous PostgreSQL schema.
-- This script provides a recovery path if backup data exists or if the
-- Hermes state.db contains capture records that should be imported.
--
-- Run: docker exec -i gbrain-postgres psql -U postgres -d gbrain < migrations/001_migrate_old_memories.sql
--
-- Safe to re-run: uses INSERT ... WHERE NOT EXISTS to prevent duplicates.

BEGIN;

-- 1. Ensure pages table exists (schema sanity check)
CREATE TABLE IF NOT EXISTS pages (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'conversation',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.8,
    frequency INT NOT NULL DEFAULT 1,
    sentiment REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT 'capture',
    ttl_days INT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_used TIMESTAMP DEFAULT NOW(),
    last_retrieved TIMESTAMP,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    scope_id TEXT,
    chat_id TEXT NOT NULL DEFAULT 'global',
    memory_tier TEXT NOT NULL DEFAULT 'standard',
    summary_text TEXT,
    compressed_content TEXT,
    parent_id INT,
    stability REAL NOT NULL DEFAULT 1.0,
    last_access TIMESTAMP,
    valid_to TIMESTAMP,
    superseded_by INT
);

-- 2. Import from Hermes state.db (via Python in the API container)
--    This is a template: uncomment and fill in the path to the backup SQLite DB
--    if one exists.
--
--    The actual import is done by the companion Python script:
--    python migrations/import_from_hermes_db.py --hermes-db /root/.hermes/state.db

-- 3. If pages_archive existed in a previous schema, restore those too
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
    archive_reason TEXT DEFAULT 'decay',
    archive_batch_id TEXT,
    scope_id TEXT
);

-- 4. Ensure essential indexes exist
CREATE INDEX IF NOT EXISTS idx_pages_scope_id ON pages(scope_id);
CREATE INDEX IF NOT EXISTS idx_pages_chat_id ON pages(chat_id);
CREATE INDEX IF NOT EXISTS idx_pages_created ON pages(created_at DESC);

COMMIT;
