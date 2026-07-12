"""Memory optimizer: decay, archive, restore, review, dry-run."""

import uuid
import logging
from hermesclaw.db import connect_db
from hermesclaw.scoring import build_scope_filter
from hermesclaw.consolidation import apply_tier_assignments, compute_tier_stats, find_consolidation_candidates, consolidate_similar_memories

logger = logging.getLogger("hermesclaw.optimizer")


def run_decay_and_archive_once() -> dict:
    """Ebbinghaus-aware decay: archive near-forgotten memories, gently reduce stability of aged ones."""
    with connect_db() as conn:
        with conn.cursor() as cur:
            # Ebbinghaus decay: reduce stability for memories untouched >30 days
            cur.execute(
                """UPDATE pages
                   SET stability = GREATEST(0.5, stability * 0.97),
                       updated_at = NOW()
                   WHERE is_archived = FALSE
                     AND last_access < NOW() - INTERVAL '30 days'
                     AND stability > 0.5"""
            )
            decayed_count = cur.rowcount

            # Archive: memories with low confidence OR very low Ebbinghaus retention
            cur.execute(
                """WITH stale AS (
                    SELECT *,
                           exp(-EXTRACT(EPOCH FROM (NOW() - COALESCE(last_access, last_used, created_at))) / 86400.0
                               / GREATEST(stability, 0.01)) AS retention
                    FROM pages
                    WHERE is_archived = FALSE
                      AND (
                        (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                        OR confidence < 0.1
                        OR exp(-EXTRACT(EPOCH FROM (NOW() - COALESCE(last_access, last_used, created_at))) / 86400.0
                               / GREATEST(stability, 0.01)) < 0.05
                      )
                )
                INSERT INTO pages_archive (
                    page_id, content, memory_type, importance, confidence, frequency,
                    sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, archive_reason
                )
                SELECT id, content, memory_type, importance, confidence, frequency,
                       sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved,
                       CASE
                         WHEN ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW() THEN 'ttl_expired'
                         WHEN confidence < 0.1 THEN 'low_confidence'
                         ELSE 'ebbinghaus_decay'
                       END
                FROM stale"""
            )
            archived_count = cur.rowcount

            cur.execute(
                """DELETE FROM pages
                   WHERE is_archived = FALSE
                     AND (
                       (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                       OR confidence < 0.1
                       OR exp(-EXTRACT(EPOCH FROM (NOW() - COALESCE(last_access, last_used, created_at))) / 86400.0
                              / GREATEST(stability, 0.01)) < 0.05
                     )"""
            )
            deleted_count = cur.rowcount
            conn.commit()

    return {"decayed": decayed_count, "archived": archived_count, "deleted": deleted_count}


def get_optimizer_review(
    limit: int = 25,
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    selected_scope: str | None = None,
) -> dict:
    scope_clause, scope_params = build_scope_filter(selected_scope, "p.scope_id")
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    p.id, p.content, p.memory_type, p.importance, p.confidence,
                    p.frequency, p.ttl_days,
                    ROUND(EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_used, p.created_at))) / 86400.0, 2) AS age_days,
                    CASE
                        WHEN p.ttl_days IS NOT NULL AND p.created_at + (p.ttl_days || ' days')::interval < NOW() THEN 'ttl_expired'
                        WHEN p.confidence < %s THEN 'low_confidence'
                        WHEN COALESCE(p.last_used, p.created_at) < NOW() - (%s || ' days')::interval THEN 'stale'
                        ELSE 'healthy'
                    END AS review_reason
                FROM pages p
                WHERE p.is_archived = FALSE
                    {scope_clause}
                  AND (
                        p.confidence < %s
                        OR COALESCE(p.last_used, p.created_at) < NOW() - (%s || ' days')::interval
                        OR (p.ttl_days IS NOT NULL AND p.created_at + (p.ttl_days || ' days')::interval < NOW())
                  )
                ORDER BY p.confidence ASC, age_days DESC
                LIMIT %s
                """,
                (*scope_params, confidence_threshold, stale_days, confidence_threshold, stale_days, limit),
            )
            pending_rows = cur.fetchall()

            cur.execute(
                """
                SELECT page_id, content, memory_type, importance, confidence, frequency,
                       ROUND(EXTRACT(EPOCH FROM (NOW() - archived_at)) / 86400.0, 2) AS days_since_archived,
                       archive_reason, archived_at
                FROM pages_archive
                ORDER BY archived_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            archived_rows = cur.fetchall()

    pending_review = [
        {
            "id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "ttl_days": r[6],
            "age_days": float(r[7] or 0.0),
            "review_reason": r[8],
        }
        for r in pending_rows
    ]
    archived_recent = [
        {
            "page_id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "days_since_archived": float(r[6] or 0.0),
            "archive_reason": r[7],
            "archived_at": str(r[8]),
        }
        for r in archived_rows
    ]

    return {
        "thresholds": {
            "stale_days": stale_days,
            "confidence_threshold": confidence_threshold,
            "limit": limit,
        },
        "pending_review": pending_review,
        "archived_recent": archived_recent,
        "summary": {"pending_count": len(pending_review), "recent_archived_count": len(archived_recent)},
    }


def get_optimizer_dry_run(
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    limit: int = 25,
    selected_scope: str | None = None,
) -> dict:
    scope_clause, scope_params = build_scope_filter(selected_scope, "scope_id")
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM pages
                WHERE is_archived = FALSE
                    {scope_clause}
                    AND last_used < NOW() - INTERVAL '7 days'
                """,
                (*scope_params,),
            )
            would_decay_count = int(cur.fetchone()[0])

            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM pages
                WHERE is_archived = FALSE
                    {scope_clause}
                    AND (
                    (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                    OR confidence < 0.18
                    )
                """,
                (*scope_params,),
            )
            would_archive_count = int(cur.fetchone()[0])

            cur.execute(
                f"""
                SELECT id, content, memory_type, importance, confidence, frequency,
                    ROUND(EXTRACT(EPOCH FROM (NOW() - COALESCE(last_used, created_at))) / 86400.0, 2) AS age_days,
                    CASE
                        WHEN ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW() THEN 'ttl_expired'
                        WHEN confidence < 0.18 THEN 'low_confidence'
                        WHEN last_used < NOW() - INTERVAL '7 days' THEN 'stale_decay_candidate'
                        ELSE 'review_candidate'
                    END AS dry_run_reason
                FROM pages
                WHERE is_archived = FALSE
                    {scope_clause}
                    AND (
                    (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                    OR confidence < 0.18
                    OR last_used < NOW() - INTERVAL '7 days'
                    OR confidence < %s
                    OR COALESCE(last_used, created_at) < NOW() - (%s || ' days')::interval
                    )
                ORDER BY
                    CASE
                        WHEN ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW() THEN 0
                        WHEN confidence < 0.18 THEN 1
                        WHEN last_used < NOW() - INTERVAL '7 days' THEN 2
                        ELSE 3
                    END,
                    confidence ASC,
                    age_days DESC
                LIMIT %s
                """,
                (*scope_params, confidence_threshold, stale_days, limit),
            )
            sample_rows = cur.fetchall()

    sample = [
        {
            "id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "age_days": float(r[6] or 0.0),
            "dry_run_reason": r[7],
        }
        for r in sample_rows
    ]

    return {
        "thresholds": {
            "stale_days": stale_days,
            "confidence_threshold": confidence_threshold,
            "limit": limit,
        },
        "would_decay_count": would_decay_count,
        "would_archive_count": would_archive_count,
        "sample": sample,
    }


def archive_selected_pages(
    page_ids: list[int],
    archive_reason: str = "manual_review",
    archive_batch_id: str | None = None,
) -> dict:
    selected_ids = sorted({int(pid) for pid in page_ids if int(pid) > 0})
    if not selected_ids:
        return {"requested": 0, "archived": 0, "deleted": 0, "ids": [], "archive_batch_id": archive_batch_id}

    batch_id = archive_batch_id or str(uuid.uuid4())

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH selected AS (
                    SELECT *
                    FROM pages
                    WHERE id = ANY(%s)
                      AND is_archived = FALSE
                )
                INSERT INTO pages_archive (
                    archive_batch_id, page_id, content, memory_type, importance, confidence, frequency,
                    sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, archive_reason
                )
                SELECT %s, id, content, memory_type, importance, confidence, frequency,
                       sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, %s
                FROM selected
                """,
                (selected_ids, batch_id, archive_reason),
            )
            archived_count = cur.rowcount

            cur.execute(
                "DELETE FROM pages WHERE id = ANY(%s) AND is_archived = FALSE",
                (selected_ids,),
            )
            deleted_count = cur.rowcount
            conn.commit()

    return {
        "requested": len(selected_ids),
        "archived": archived_count,
        "deleted": deleted_count,
        "ids": selected_ids,
        "archive_reason": archive_reason,
        "archive_batch_id": batch_id,
    }


def run_tier_assignment() -> dict:
    """Recalculate memory tiers and run consolidation."""
    with connect_db() as conn:
        apply_tier_assignments(conn)
        tiers = compute_tier_stats(conn)
        candidates = find_consolidation_candidates(conn, limit=30)
        consolidation = {"groups": 0, "memories_consolidated": 0}
        if len(candidates) >= 2:
            consolidation = consolidate_similar_memories(conn, candidates)
        conn.commit()
    return {"tiers": tiers, "consolidation": consolidation}


def get_latest_manual_archive_batch_id() -> str | None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT archive_batch_id
                FROM pages_archive
                WHERE archive_reason = 'manual_selected'
                  AND archive_batch_id IS NOT NULL
                ORDER BY archived_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    return row[0] if row else None


def restore_archive_batch(archive_batch_id: str) -> dict:
    if not archive_batch_id:
        return {"restored": 0, "deleted_from_archive": 0, "archive_batch_id": archive_batch_id}

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pages (
                    content, memory_type, importance, confidence, frequency,
                    sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, is_archived
                )
                SELECT
                    content,
                    COALESCE(memory_type, 'conversation'),
                    COALESCE(importance, 0.5),
                    COALESCE(confidence, 0.8),
                    COALESCE(frequency, 1),
                    COALESCE(sentiment, 0.0),
                    COALESCE(source, 'restore'),
                    ttl_days,
                    COALESCE(created_at, NOW()),
                    NOW(),
                    COALESCE(last_used, NOW()),
                    last_retrieved,
                    FALSE
                FROM pages_archive
                WHERE archive_batch_id = %s
                """,
                (archive_batch_id,),
            )
            restored_count = cur.rowcount

            cur.execute(
                "DELETE FROM pages_archive WHERE archive_batch_id = %s",
                (archive_batch_id,),
            )
            deleted_archive_count = cur.rowcount
            conn.commit()

    return {
        "restored": restored_count,
        "deleted_from_archive": deleted_archive_count,
        "archive_batch_id": archive_batch_id,
    }
