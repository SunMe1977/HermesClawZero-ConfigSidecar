-- Migration 002: 2M-scale optimizations — materialized views + covering indexes
--
-- At 2M pages, COUNT(*) and GROUP BY queries on the dashboard become slow.
-- This migration adds:
--   1. pages_stats_mv — materialized view for dashboard aggregates
--   2. Better FTS index for lexical search at scale
--   3. Partial index for active (non-archived) memories
--   4. Composite index for the hybrid search CTE
--
-- Safe to re-run: uses IF NOT EXISTS / IF EXISTS.

BEGIN;

-- 1. Materialized view for dashboard stats
--    Refreshed every optimizer cycle (every 6h by default)
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
FROM pages;
COMMENT ON MATERIALIZED VIEW pages_stats_mv IS 'Dashboard aggregate stats, refreshed by optimizer.';

-- 2. Index for active-memory-only queries (used by search, nudge, dashboard)
CREATE INDEX IF NOT EXISTS idx_pages_active_hybrid
    ON pages(importance DESC, frequency DESC, last_used DESC)
    WHERE is_archived = FALSE;

-- 3. Better FTS index for lexical search at 2M (GIN with fast update)
DROP INDEX IF EXISTS idx_pages_fts_content;
CREATE INDEX IF NOT EXISTS idx_pages_fts_content
    ON pages USING GIN (to_tsvector('english', content))
    WHERE is_archived = FALSE;

-- 4. Composite index for search ORDER BY + scope filter
CREATE INDEX IF NOT EXISTS idx_pages_search_scope
    ON pages(scope_id, created_at DESC)
    WHERE is_archived = FALSE;

-- 5. Materialized view for scope-level stats (used by dashboard scope picker)
CREATE MATERIALIZED VIEW IF NOT EXISTS pages_scope_stats_mv AS
SELECT
    scope_id,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE is_archived = FALSE) AS active,
    COUNT(*) FILTER (WHERE memory_type = 'fact') AS facts,
    COUNT(*) FILTER (WHERE memory_type = 'preference') AS preferences,
    COUNT(*) FILTER (WHERE memory_type = 'project') AS projects,
    COUNT(*) FILTER (WHERE memory_type = 'skill') AS skills,
    COUNT(*) FILTER (WHERE memory_type = 'conversation') AS conversations,
    NOW() AS computed_at
FROM pages
WHERE scope_id IS NOT NULL
GROUP BY scope_id;
COMMENT ON MATERIALIZED VIEW pages_scope_stats_mv IS 'Per-scope aggregate stats, refreshed by optimizer.';

CREATE UNIQUE INDEX IF NOT EXISTS idx_pss_mv_scope ON pages_scope_stats_mv(scope_id);

COMMIT;
