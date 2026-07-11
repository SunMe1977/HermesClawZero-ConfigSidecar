"""
Reflection Agent: periodic analysis of all memories for contradictions, 
knowledge gaps, and summaries. Inspired by MemGPT's self-reflection 
and Supermemory's knowledge updates.

Runs as part of the optimizer cycle.
"""
import json, logging
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger("hermesclaw.reflection")

REFLECTION_INTERVAL_SECONDS = 86400  # once per day


def analyze_memories(conn, llm_generate=None, dry_run: bool = False) -> dict:
    """Analyze all non-archived memories and produce reflection insights.
    
    Returns:
        contradictions: list of conflicting memory pairs
        knowledge_gaps: topics that need more info
        summary: high-level summary of what's known
        stats: counts of memories by type and tier
    """
    with conn.cursor() as cur:
        # Get all memories grouped by scope
        cur.execute(
            """SELECT p.id, p.content, p.memory_type, p.importance, p.confidence,
                      p.scope_id, p.frequency
               FROM pages p
               WHERE p.is_archived = FALSE AND p.parent_id IS NULL
               ORDER BY p.scope_id, p.importance DESC"""
        )
        memories = [dict(zip(["id","content","type","importance","confidence","scope","freq"], r)) for r in cur.fetchall()]

    # Group by scope
    scopes = defaultdict(list)
    for m in memories:
        scopes[m["scope"] or "global"].append(m)

    contradictions = []
    summaries = {}

    for scope, mems in scopes.items():
        if len(mems) < 2:
            summaries[scope] = {"count": len(mems), "note": "too few memories for analysis"}
            continue

        # Stats
        types = defaultdict(int)
        tiers = {"high": 0, "medium": 0, "low": 0}
        for m in mems:
            types[m["type"]] += 1
            if m["importance"] >= 0.7: tiers["high"] += 1
            elif m["importance"] >= 0.4: tiers["medium"] += 1
            else: tiers["low"] += 1

        # Simple contradiction detection: same type, opposite sentiments
        for i in range(len(mems)):
            for j in range(i + 1, len(mems)):
                if mems[i]["type"] == mems[j]["type"]:
                    ci, cj = mems[i]["content"].lower(), mems[j]["content"].lower()
                    # Negation-based contradiction heuristic
                    negations = ["not ", "don't ", "doesn't ", "never ", "cannot ", "won't "]
                    has_neg_i = any(n in ci for n in negations)
                    has_neg_j = any(n in cj for n in negations)
                    if has_neg_i != has_neg_j and len(set(ci.split()) & set(cj.split())) > 3:
                        contradictions.append({
                            "id_a": mems[i]["id"], "content_a": mems[i]["content"][:200],
                            "id_b": mems[j]["id"], "content_b": mems[j]["content"][:200],
                            "type": mems[i]["type"], "scope": scope,
                        })

        # LLM-powered summary if generator available
        if llm_generate and len(mems) >= 3:
            prompt = (
                f"Summarize the following {len(mems)} memories for scope '{scope}' in 2-3 sentences. "
                "Highlight key facts, preferences, and projects. Be concise.\n\n"
                + "\n".join(f"- [{m['type']}] {m['content'][:300]}" for m in mems[:15])
            )
            try:
                resp = llm_generate(model="llama3.1:8b", prompt=prompt)
                summaries[scope] = {
                    "count": len(mems),
                    "types": dict(types),
                    "tiers": tiers,
                    "contradictions_found": sum(1 for c in contradictions if c["scope"] == scope),
                    "llm_summary": resp["response"][:500],
                }
            except Exception:
                summaries[scope] = {"count": len(mems), "types": dict(types), "tiers": tiers, "llm_summary": None}
        else:
            summaries[scope] = {"count": len(mems), "types": dict(types), "tiers": tiers}

    return {
        "contradictions": contradictions,
        "summaries": summaries,
        "stats": {
            "total_memories": len(memories),
            "scopes": len(scopes),
            "contradictions_found": len(contradictions),
        },
    }


def resolve_contradiction(conn, contradiction: dict, llm_generate=None, dry_run: bool = False) -> dict | None:
    """Attempt to resolve a contradiction by LLM arbitration.
    
    Returns a dict with resolved_content, confidence, and which id to keep.
    """
    if not llm_generate:
        return None
    
    prompt = (
        f"Two memories contradict each other. "
        f"Memory A: \"{contradiction['content_a']}\"\n"
        f"Memory B: \"{contradiction['content_b']}\"\n\n"
        "Which one is more likely correct? If both could be true (e.g. preference change), "
        "return a merged version. Return JSON: "
        '{"resolution": "keep_a|keep_b|merge", "merged_content": "...", "confidence": <0-1>}'
    )
    try:
        resp = llm_generate(model="llama3.1:8b", prompt=prompt)
        raw = resp["response"].strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("\n", 1)[0]
        result = json.loads(raw)
        
        if dry_run:
            return result

        with conn.cursor() as cur:
            if result.get("resolution") == "keep_a":
                cur.execute("UPDATE pages SET confidence = LEAST(1.0, confidence + 0.1) WHERE id = %s",
                            (contradiction["id_a"],))
                cur.execute("UPDATE pages SET confidence = GREATEST(0.1, confidence - 0.1), parent_id = %s WHERE id = %s",
                            (contradiction["id_a"], contradiction["id_b"]))
            elif result.get("resolution") == "keep_b":
                cur.execute("UPDATE pages SET confidence = LEAST(1.0, confidence + 0.1) WHERE id = %s",
                            (contradiction["id_b"],))
                cur.execute("UPDATE pages SET confidence = GREATEST(0.1, confidence - 0.1), parent_id = %s WHERE id = %s",
                            (contradiction["id_b"], contradiction["id_a"]))
            elif result.get("resolution") == "merge" and result.get("merged_content"):
                merged = result["merged_content"]
                # Create new merged memory, reparent both
                cur.execute(
                    """INSERT INTO pages (content, memory_type, importance, confidence, frequency,
                                          source, scope_id, chat_id, memory_tier, updated_at, last_used)
                       VALUES (%s, %s, %s, %s, %s, 'reflection', %s, 'global', 'hot', NOW(), NOW())
                       RETURNING id""",
                    (merged, contradiction["type"],
                     max(0.7, contradiction.get("confidence", 0.7)),
                     0.9, 1, contradiction.get("scope")),
                )
                new_id = cur.fetchone()[0]
                cur.execute("UPDATE pages SET parent_id = %s WHERE id IN (%s, %s)",
                            (new_id, contradiction["id_a"], contradiction["id_b"]))
        conn.commit()
        return result
    except Exception as ex:
        logger.debug("Contradiction resolution failed: %s", ex)
        return None
