"""
Memory consolidation: summarization, tier management, compression, versioning.

Inspired by LangMem's memory tiering and Supermemory's consolidation pipeline.
Runs periodically to consolidate similar memories into higher-tier summaries.
"""
import json, logging, math
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("hermesclaw.consolidation")

# ── Tier definitions ──
# hot:   currently relevant, accessed within hours
# warm:  accessed within days, medium importance
# cold:  older, low importance, archival candidates
# standard: default tier for newly captured memories

TIER_THRESHOLDS = {
    "hot": {"importance": 0.8, "max_age_days": 1, "max_memories": 50},
    "warm": {"importance": 0.5, "max_age_days": 14, "max_memories": 200},
    "cold": {"importance": 0.0, "max_age_days": 365, "max_memories": 2000},
}

CONSOLIDATION_SIMILARITY_THRESHOLD = 0.85  # cosine similarity
MAX_COMPRESSION_RATIO = 0.4  # target: 40% of original length


# ── Tier assignment ──

def assign_tier(importance: float, age_days: float, confidence: float) -> str:
    """Determine memory tier based on importance, age, and confidence."""
    if importance >= 0.8 and age_days <= 1 and confidence >= 0.6:
        return "hot"
    if importance >= 0.5 and age_days <= 14:
        return "warm"
    return "standard"  # cold is assigned by the optimizer


def compute_tier_stats(conn) -> dict:
    """Count memories per tier."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT memory_tier, COUNT(*) FROM pages WHERE is_archived = FALSE GROUP BY memory_tier"
        )
        tiers = {r[0]: r[1] for r in cur.fetchall()}
    return {
        "hot": tiers.get("hot", 0),
        "warm": tiers.get("warm", 0),
        "standard": tiers.get("standard", 0),
        "cold": tiers.get("cold", 0),
    }


def apply_tier_assignments(conn):
    """Recalculate tier for all non-archived memories."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE pages SET memory_tier = 'cold'
               WHERE is_archived = FALSE
                 AND importance < 0.3
                 AND confidence < 0.4
                 AND last_used < NOW() - INTERVAL '30 days'"""
        )
        cur.execute(
            """UPDATE pages SET memory_tier = 'hot'
               WHERE is_archived = FALSE
                 AND importance >= 0.75
                 AND last_used > NOW() - INTERVAL '2 days'"""
        )
        cur.execute(
            """UPDATE pages SET memory_tier = 'warm'
               WHERE is_archived = FALSE
                 AND memory_tier NOT IN ('hot', 'cold')
                 AND (importance >= 0.5 OR last_used > NOW() - INTERVAL '14 days')"""
        )
        conn.commit()


# ── Compression ──

def compress_content(content: str, target_ratio: float = MAX_COMPRESSION_RATIO) -> str:
    """Intelligent content compression: keep first sentence, key entities, last sentence."""
    if len(content) < 200:
        return content  # too short to compress

    lines = content.strip().split("\n")
    if len(lines) <= 3:
        # Single paragraph: keep first 40% and last 20%
        words = content.split()
        max_words = max(20, int(len(words) * target_ratio))
        if len(words) <= max_words:
            return content
        head = " ".join(words[:max_words // 2])
        tail = " ".join(words[-max_words // 4:])
        return f"{head} […] {tail}"

    # Multi-line: keep first 2, last 1, compress middle
    head = lines[0].strip()
    tail = lines[-1].strip()
    header = f'{head}…' if len(head) > 60 else head

    # Count middle
    middle = lines[1:-1]
    compressed_middle = []
    char_budget = max(80, int(sum(len(l) for l in middle) * target_ratio))
    used = 0
    for line in middle:
        if used >= char_budget:
            compressed_middle.append("[…]")
            break
        compressed_middle.append(line[:char_budget - used])
        used += len(line)

    parts = [header] + compressed_middle
    if tail != head:
        parts.append(tail)

    return "\n".join(parts)


def store_compressed_version(conn, page_id: int, content: str):
    """Store a compressed version and create a memory version record."""
    compressed = compress_content(content)
    if compressed != content:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pages SET compressed_content = %s WHERE id = %s",
                (compressed, page_id),
            )
            # Track version
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM memory_versions WHERE page_id = %s",
                (page_id,),
            )
            max_ver = cur.fetchone()[0]
            cur.execute(
                "SELECT memory_type, importance, confidence FROM pages WHERE id = %s",
                (page_id,),
            )
            page = cur.fetchone()
            if page:
                cur.execute(
                    """INSERT INTO memory_versions (page_id, content, memory_type, importance, confidence, version, change_reason)
                       VALUES (%s, %s, %s, %s, %s, %s, 'compression')""",
                    (page_id, compressed, page[0], page[1], page[2], max_ver + 1),
                )
            conn.commit()
    return compressed


# ── Consolidation (summarize similar memories) ──

def find_consolidation_candidates(conn, scope_id: str | None = None, limit: int = 50) -> list[dict]:
    """Find memories close in embedding space within same scope for potential consolidation."""
    with conn.cursor() as cur:
        scope_filter = "AND p.scope_id = %s" if scope_id else ""
        scope_params = [scope_id] if scope_id else []

        # Get memories with their embeddings
        cur.execute(
            f"""SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.scope_id, p.chat_id, e.embedding
                FROM pages p
                JOIN embeddings e ON e.page_id = p.id
                WHERE p.is_archived = FALSE
                  AND p.memory_tier != 'hot'
                  {scope_filter}
                ORDER BY p.last_used DESC
                LIMIT %s""",
            (*scope_params, limit),
        )
        return [
            {"id": r[0], "content": r[1], "memory_type": r[2],
             "importance": r[3], "confidence": r[4],
             "scope_id": r[5], "chat_id": r[6], "embedding": r[7]}
            for r in cur.fetchall()
        ]


def consolidate_similar_memories(conn, memories: list[dict], llm_generate=None) -> dict:
    """Group similar memories and create consolidated summaries.

    Returns stats about what was consolidated.
    """
    if len(memories) < 2:
        return {"groups": 0, "memories_consolidated": 0}

    # Simple clustering: group by memory_type first, then by embedding similarity
    groups = defaultdict(list)
    for m in memories:
        groups[m["memory_type"]].append(m)

    consolidated = 0
    grouped_count = 0

    for mtype, items in groups.items():
        if len(items) < 2:
            continue
        # Order by importance desc
        items.sort(key=lambda x: x["importance"], reverse=True)

        # Take top 5 most important per type
        cluster = items[:5]
        if len(cluster) < 2:
            continue

        # Create a consolidated summary
        contents = [c["content"] for c in cluster]
        summary = _summarize_cluster(contents, mtype, llm_generate)

        # Find the highest-importance memory to serve as parent
        parent = cluster[0]
        # Get parent's scope_id and chat_id
        parent_scope = parent.get("scope_id")
        parent_chat = parent.get("chat_id", "global")

        with conn.cursor() as cur:
            # Store the consolidated memory
            cur.execute(
                """INSERT INTO pages (content, memory_type, importance, confidence, frequency,
                                      sentiment, source, scope_id, chat_id, memory_tier,
                                      summary_text, parent_id, updated_at, last_used)
                   VALUES (%s, %s, %s, %s, %s, 0, 'consolidation', %s, %s, 'warm',
                           %s, %s, NOW(), NOW())
                   RETURNING id""",
                (
                    summary, mtype, parent["importance"] * 1.05,
                    min(1.0, parent["confidence"] + 0.05),
                    sum(c.get("frequency", 1) for c in cluster),
                    parent_scope, parent_chat,
                    summary, parent["id"],
                ),
            )
            new_id = cur.fetchone()[0]
            conn.commit()

        # Mark consolidated memories with parent_id
        with conn.cursor() as cur:
            for m in cluster:
                cur.execute(
                    "UPDATE pages SET parent_id = %s, memory_tier = 'cold' WHERE id = %s",
                    (new_id, m["id"]),
                )
            conn.commit()

        consolidated += 1
        grouped_count += len(cluster)

    return {"groups": consolidated, "memories_consolidated": grouped_count}


def _summarize_cluster(contents: list[str], mtype: str, llm_generate=None) -> str:
    """Create a combined summary from a cluster of similar memories."""
    if llm_generate:
        prompt = (
            f"Combine the following {mtype} memories into one concise summary "
            f"(max 3 sentences) that preserves all key facts:\n\n"
            + "\n---\n".join(f"- {c[:500]}" for c in contents[:5])
        )
        try:
            resp = llm_generate(model="llama3.1:8b", prompt=prompt)
            return resp["response"][:1000]
        except Exception:
            pass

    # Cold path: concatenate with deduplication
    seen = set()
    parts = []
    for c in contents:
        key = c.lower().strip()[:100]
        if key not in seen:
            seen.add(key)
            parts.append(c.strip())
    return "\n".join(parts[:3])
