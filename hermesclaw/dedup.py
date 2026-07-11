"""
Auto-merge: periodic deduplication + similarity merging of memories.
Inspired by Mem0's token-efficient merging and Supermemory's consolidation.

Runs as part of the optimizer cycle — finds near-duplicate memories
and merges them automatically.
"""
import logging
from collections import defaultdict
from hermesclaw.db import connect_db, embedding_to_pgvector_literal
from hermesclaw.scoring import compute_hybrid_score

logger = logging.getLogger("hermesclaw.dedup")

MERGE_SIMILARITY_THRESHOLD = 0.92  # cosine similarity for dedup
MERGE_CONTENT_THRESHOLD = 0.78     # lower threshold for content-similar merge


def find_and_merge_duplicates(dry_run: bool = False) -> dict:
    """Find near-duplicate memories within the same scope and merge them.
    
    Two-phase approach:
    Phase 1 — Hard dedup: cosine distance < 0.08 (threshold 0.92)
    Phase 2 — Soft merge: cosine distance < 0.22 (threshold 0.78) within same type
    """
    stats = {"hard_dedup": 0, "soft_merge": 0, "errors": []}

    with connect_db() as conn:
        # Get all non-archived memories with embeddings
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.id, p.content, p.memory_type, p.scope_id, p.chat_id, p.importance, e.embedding
                   FROM pages p
                   JOIN embeddings e ON e.page_id = p.id
                   WHERE p.is_archived = FALSE
                     AND p.parent_id IS NULL
                   ORDER BY p.id ASC"""
            )
            memories = cur.fetchall()

    if not memories:
        return {"hard_dedup": 0, "soft_merge": 0}

    # Group by (scope_id, chat_id) for local dedup
    groups = defaultdict(list)
    for m in memories:
        key = (m[3] or "none", m[4] or "global")
        groups[key].append(m)

    for key, group in groups.items():
        if len(group) < 2:
            continue
        scope, chat = key

        # Phase 1: Hard dedup (vector distance)
        merged_ids = set()
        for i in range(len(group)):
            if group[i][0] in merged_ids:
                continue
            for j in range(i + 1, len(group)):
                if group[j][0] in merged_ids:
                    continue
                # Compute cosine distance from raw vectors
                vi = group[i][6]
                vj = group[j][6]
                if vi and vj:
                    # Handle both list and string representations from pgvector
                    if isinstance(vi, str):
                        import json
                        try: vi = json.loads(vi)
                        except: continue
                    if isinstance(vj, str):
                        import json
                        try: vj = json.loads(vj)
                        except: continue
                    if not isinstance(vi, (list, tuple)) or not isinstance(vj, (list, tuple)):
                        continue
                    # Cosine similarity without numpy
                    dot = sum(float(a)*float(b) for a,b in zip(vi, vj))
                    norm_i = sum(float(a)*float(a) for a in vi)**0.5
                    norm_j = sum(float(b)*float(b) for b in vj)**0.5
                    sim = float(dot / (norm_i * norm_j)) if (norm_i * norm_j) > 0 else 0

                    # Phase 1: Hard dedup (near-identical)
                    if sim >= MERGE_SIMILARITY_THRESHOLD:
                        # Merge lower-importance into higher-importance
                        if group[i][5] >= group[j][5]:
                            keep, remove = group[i], group[j]
                        else:
                            keep, remove = group[j], group[i]
                        if not dry_run:
                            _merge_two(conn, keep[0], remove[0], keep[1], remove[1])
                        merged_ids.add(remove[0])
                        stats["hard_dedup"] += 1
                        logger.info("Hard dedup: %s → %s (sim=%.3f)", remove[0], keep[0], sim)

                    # Phase 2: Soft merge (same type, semantically similar)
                    elif (sim >= MERGE_CONTENT_THRESHOLD
                          and group[i][2] == group[j][2]  # same memory_type
                          and group[i][2] not in ("conversation",)):  # skip generic
                        keep, remove = (group[i], group[j]) if group[i][5] >= group[j][5] else (group[j], group[i])
                        if not dry_run:
                            _merge_two(conn, keep[0], remove[0], keep[1], remove[1])
                        merged_ids.add(remove[0])
                        stats["soft_merge"] += 1
                        logger.info("Soft merge: %s → %s (sim=%.3f, type=%s)", remove[0], keep[0], sim, group[i][2])

    if not dry_run:
        conn.commit()

    return stats


def _merge_two(conn, keep_id: int, remove_id: int, keep_content: str, remove_content: str):
    """Merge two memories: append content, transfer frequency + importance, reparent."""
    with conn.cursor() as cur:
        # Transfer frequency
        cur.execute("SELECT frequency FROM pages WHERE id = %s", (remove_id,))
        remove_freq = (cur.fetchone() or [0])[0]
        # Append content
        merged = keep_content + "\n\n[merged from #" + str(remove_id) + "]: " + remove_content[:500]
        cur.execute(
            "UPDATE pages SET content = %s, frequency = frequency + %s, "
            "importance = LEAST(1.0, importance + 0.02), updated_at = NOW() "
            "WHERE id = %s",
            (merged, remove_freq, keep_id),
        )
        # Re-parent removed memory
        cur.execute(
            "UPDATE pages SET parent_id = %s, memory_tier = 'cold' WHERE id = %s",
            (keep_id, remove_id),
        )
        # Regenerate embedding for merged content
        from hermesclaw.embeddings import generate_embedding
        try:
            new_emb = generate_embedding(merged[:2000])
            emb_str = embedding_to_pgvector_literal(new_emb)
            cur.execute("UPDATE embeddings SET embedding = %s::vector WHERE page_id = %s", (emb_str, keep_id))
        except Exception:
            pass
