"""
Knowledge Graph: entity extraction, relationship tracking, graph queries.

Inspired by Cognee's graph-based memory architecture and Supermemory's
entity extraction pipeline. Designed for zero external API dependencies —
uses Ollama for optional LLM-powered extraction, regex/rule-based for cold path.
"""
import re, json, logging
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("hermesclaw.graph")

# ── Rule-based entity patterns (cold path, no LLM needed) ──
_ENTITY_PATTERNS = [
    ("person", r'(?i)(?:user|ich|mein|my name is |call me |i am |my name\'s )([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)'),
    ("project", r'(?i)(?:project|repo|repository|app)["\']?([A-Za-z][A-Za-z0-9_-]+)'),
    ("tool", r'(?i)(?:using|use|tool|cli|sdk|api|framework|library|db|database)[\s:](\w[\w.-]*)'),
    ("language", r'(?i)\b(Python|Rust|Go|TypeScript|JavaScript|Java|C\+\+|C#|Ruby|PHP|Kotlin|Swift)\b'),
    ("os", r'(?i)\b(Windows|Linux|macOS|Ubuntu|Debian|Fedora|Alpine|Docker|Kubernetes)\b'),
    ("service", r'(?i)(?:service|platform|hosted at|runs? on|deployed to)\s+(\w[\w.-]+)'),
    ("skill", r'(?i)(?:skill|plugin|extension|add.?on)[\s:]+(\w[\w\s-]{2,40})'),
    ("concept", r'(?i)(?:memory|embedding|vector|graph|rag|agent|pipeline|workflow|autonomous)\b'),
]

# ── Relationship heuristics ──
_RELATION_PATTERNS = [
    ("uses", r'(?i)(\w[\w.-]*)\s+uses?\s+(\w[\w.-]*)'),
    ("built_with", r'(?i)(\w[\w.-]*)\s+(?:built|developed|written|implemented)\s+(?:with|in|using)\s+(\w[\w.-]*)'),
    ("deployed_on", r'(?i)(\w[\w.-]*)\s+(?:deployed|runs?|hosted)\s+(?:on|at|via)\s+(\w[\w.-]*)'),
    ("depends_on", r'(?i)(\w[\w.-]*)\s+(?:depends?|requires?|needs?)\s+(\w[\w.-]*)'),
    ("related_to", r'(?i)(\w[\w.-]*)\s+(?:and|with|vs|versus|compared? to)\s+(\w[\w.-]*)'),
    ("built", r'(?i)(\w[\w.-]*)\s+(?:built|created|made|developed)\s+(?:with|using|in|for)\s+(\w[\w.-]*)'),
    ("leads", r'(?i)(\w[\w.-]*)\s+(?:leads?|manages?|owns?|responsible for)\s+(\w[\w.-]*)'),
    ("part_of", r'(?i)(\w[\w.-]*)\s+(?:is part of|belongs to|member of|inside)\s+(\w[\w.-]*)'),
    ("implements", r'(?i)(\w[\w.-]*)\s+(?:implements|realizes|enables|powers)\s+(\w[\w.-]*)'),
    ("located_at", r'(?i)(\w[\w.-]*)\s+(?:located|hosted|stored|runs)\s+(?:at|on|in|via)\s+(\w[\w.-]*)'),
    ("communicates", r'(?i)(\w[\w.-]*)\s+(?:communicates?|interacts?|connects?|talks)\s+(?:with|to|via)\s+(\w[\w.-]*)'),
    ("preceded_by", r'(?i)(\w[\w.-]*)\s+(?:preceded?|came before|was before|superseded)\s+(?:by)?\s*(\w[\w.-]*)'),
]


def extract_entities(text: str) -> list[dict]:
    """Extract entities from text using rules. Returns [{name, entity_type, context}]."""
    seen = set()
    results = []
    for etype, pattern in _ENTITY_PATTERNS:
        for m in re.finditer(pattern, text):
            name = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            key = f"{name.lower()}:{etype}"
            if key not in seen and len(name) > 1 and len(name) < 80:
                seen.add(key)
                results.append({
                    "name": name[:120],
                    "entity_type": etype,
                    "context": text[max(0, m.start()-40):min(len(text), m.end()+40)],
                })
    return results


def extract_relationships(text: str, entities: list[dict]) -> list[dict]:
    """Extract pairwise relationships from text and known entities."""
    entity_names = {e["name"].lower() for e in entities}
    results = []

    for rel_type, pattern in _RELATION_PATTERNS:
        for m in re.finditer(pattern, text):
            src, tgt = m.group(1).strip(), m.group(2).strip()
            if src.lower() in entity_names and tgt.lower() in entity_names:
                results.append({
                    "source": src[:120],
                    "target": tgt[:120],
                    "relation_type": rel_type,
                    "weight": 1.0,
                })
    return results


def extract_entities_llm(text: str, llm_generate) -> list[dict]:
    """LLM-powered entity extraction (warm path). Falls back to rules on failure."""
    prompt = (
        "Extract entities from the following text. "
        "For each entity, provide name, type (person/project/tool/language/service/concept/skill), "
        "and a one-line context snippet. "
        "Return ONLY valid JSON array: [{\"name\": \"...\", \"entity_type\": \"...\", \"context\": \"...\"}]\n\n"
        f"Text: {text[:2000]}"
    )
    try:
        resp = llm_generate(model="llama3.1:8b", prompt=prompt)
        raw = resp["response"].strip()
        if raw.startswith("```"):  # strip code fences
            raw = raw.split("\n", 1)[-1].rsplit("\n", 1)[0] if "```" in raw else raw
        entities = json.loads(raw)
        if isinstance(entities, list) and len(entities) <= 30:
            return entities
    except Exception:
        logger.debug("LLM entity extraction failed, falling back to rules")
    return extract_entities(text)


# ── DB Operations ──

def upsert_entity(conn, name: str, entity_type: str, metadata: dict | None = None):
    """Insert or update an entity, returning its id."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO entities (name, entity_type, canonical_name, metadata, last_seen, frequency)
               VALUES (%s, %s, %s, %s, NOW(), 1)
               ON CONFLICT (name) DO UPDATE SET
                 last_seen = NOW(),
                 frequency = GREATEST(entities.frequency, EXCLUDED.frequency) + 1,
                 metadata = entities.metadata || %s
               RETURNING id""",
            (name[:200], entity_type[:50], name[:200],
             json.dumps(metadata or {}), json.dumps(metadata or {})),
        )
        return cur.fetchone()[0]


def record_mention(conn, entity_id: int, page_id: int, context: str | None = None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO entity_mentions (entity_id, page_id, context) VALUES (%s, %s, %s)",
            (entity_id, page_id, (context or "")[:500]),
        )


def upsert_relationship(conn, source_id: int, target_id: int, rel_type: str, weight: float = 1.0):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO relationships (source_entity_id, target_entity_id, relation_type, weight, last_seen, frequency)
               VALUES (%s, %s, %s, %s, NOW(), 1)
               ON CONFLICT (source_entity_id, target_entity_id, relation_type) DO UPDATE SET
                 last_seen = NOW(),
                 weight = GREATEST(relationships.weight, %s),
                 frequency = relationships.frequency + 1""",
            (source_id, target_id, rel_type[:50], weight, weight),
        )


def process_memory_for_graph(conn, text: str, page_id: int, use_llm: bool = False, llm_generate=None):
    """Extract entities + relationships from a memory and persist to graph tables."""
    if use_llm and llm_generate:
        entities = extract_entities_llm(text, llm_generate)
    else:
        entities = extract_entities(text)

    entity_ids = {}
    for ent in entities:
        eid = upsert_entity(conn, ent["name"], ent.get("entity_type", "unknown"), {"source": "capture"})
        entity_ids[ent["name"]] = eid
        record_mention(conn, eid, page_id, ent.get("context"))

    rels = extract_relationships(text, entities)
    for rel in rels:
        src_id = entity_ids.get(rel["source"])
        tgt_id = entity_ids.get(rel["target"])
        if src_id and tgt_id and src_id != tgt_id:
            upsert_relationship(conn, src_id, tgt_id, rel["relation_type"], rel.get("weight", 1.0))

    return {"entities": len(entity_ids), "relationships": len(rels)}


def query_entity_graph(conn, entity_name: str, depth: int = 2) -> dict:
    """Traverse the knowledge graph from an entity, returning connected nodes + edges."""
    with conn.cursor() as cur:
        # Find the entity
        cur.execute("SELECT id, name, entity_type, metadata, frequency FROM entities WHERE name = %s", (entity_name,))
        root = cur.fetchone()
        if not root:
            return {"root": None, "nodes": [], "edges": []}

        root_id = root[0]
        nodes = {root_id: {"id": root_id, "name": root[1], "type": root[2], "metadata": root[3], "frequency": root[4]}}

        visited = {root_id}
        current = {root_id}
        edges = []

        for _ in range(depth):
            if not current:
                break
            next_ids = set()

            # Outgoing
            cur.execute(
                "SELECT r.source_entity_id, r.target_entity_id, r.relation_type, r.weight "
                "FROM relationships r WHERE r.source_entity_id = ANY(%s)",
                (list(current),),
            )
            for src, tgt, rel, wt in cur.fetchall():
                edges.append({"source": src, "target": tgt, "type": rel, "weight": wt})
                if tgt not in visited:
                    next_ids.add(tgt)

            # Incoming
            cur.execute(
                "SELECT r.source_entity_id, r.target_entity_id, r.relation_type, r.weight "
                "FROM relationships r WHERE r.target_entity_id = ANY(%s)",
                (list(current),),
            )
            for src, tgt, rel, wt in cur.fetchall():
                edges.append({"source": src, "target": tgt, "type": rel, "weight": wt})
                if src not in visited:
                    next_ids.add(src)

            # Fetch new nodes
            if next_ids:
                cur.execute(
                    "SELECT id, name, entity_type, metadata, frequency FROM entities WHERE id = ANY(%s)",
                    (list(next_ids),),
                )
                for row in cur.fetchall():
                    nodes[row[0]] = {"id": row[0], "name": row[1], "type": row[2], "metadata": row[3], "frequency": row[4]}
                visited.update(next_ids)
            current = next_ids

    return {
        "root": {"id": root_id, "name": root[1], "type": root[2]},
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def get_memories_for_entity(conn, entity_name: str, limit: int = 20) -> list[dict]:
    """Get memory pages mentioning a specific entity."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, em.context, em.mentioned_at
               FROM entity_mentions em
               JOIN entities e ON e.id = em.entity_id
               JOIN pages p ON p.id = em.page_id
               WHERE e.name = %s AND p.is_archived = FALSE
               ORDER BY em.mentioned_at DESC
               LIMIT %s""",
            (entity_name, limit),
        )
        return [
            {"id": r[0], "content": r[1], "memory_type": r[2], "importance": r[3],
             "confidence": r[4], "context": r[5], "mentioned_at": str(r[6])}
            for r in cur.fetchall()
        ]


def get_top_entities(conn, limit: int = 20) -> list[dict]:
    """Get most frequently mentioned entities."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, entity_type, frequency FROM entities ORDER BY frequency DESC, last_seen DESC LIMIT %s",
            (limit,),
        )
        return [{"id": r[0], "name": r[1], "type": r[2], "frequency": r[3]} for r in cur.fetchall()]


# ── GraphRAG: Graph-augmented retrieval ──

def graph_rag_search(conn, query_entities: list[str], query_text: str = "", limit: int = 10) -> list[dict]:
    """GraphRAG: retrieve memories via entity graph traversal, then rerank by relevance.
    
    Two-phase:
    1. Find entities matching query → traverse graph depth=1 → collect all attached memories
    2. Rerank by entity frequency + recency + text relevance
    """
    seen_pages = {}  # page_id -> score
    
    for entity_name in query_entities[:5]:
        # Find this entity
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM entities WHERE name ILIKE %s", (entity_name,))
            row = cur.fetchone()
            if not row:
                continue
            eid = row[0]
            
            # Get all pages mentioning this entity
            cur.execute(
                """SELECT em.page_id, p.content, p.importance, p.confidence,
                          EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_used, p.created_at))) / 86400.0 AS age_days
                   FROM entity_mentions em
                   JOIN pages p ON p.id = em.page_id
                   WHERE em.entity_id = %s AND p.is_archived = FALSE
                   LIMIT 20""",
                (eid,),
            )
            for row2 in cur.fetchall():
                pid = row2[0]
                age = float(row2[4] or 0)
                score = 0.6 * 1.0 + 0.2 * float(row2[2]) + 0.1 * float(row2[3]) + 0.1 * max(0.0, 1.0 - age / 30.0)
                if pid in seen_pages:
                    seen_pages[pid]["score"] = max(seen_pages[pid]["score"], score)
                    seen_pages[pid]["entities"].append(entity_name)
                else:
                    seen_pages[pid] = {"id": pid, "content": row2[1], "importance": row2[2],
                                       "confidence": row2[3], "age_days": row2[4],
                                       "entities": [entity_name], "score": score}
            
            # Traverse relationships (depth=1)
            cur.execute(
                """SELECT r.target_entity_id FROM relationships r WHERE r.source_entity_id = %s
                   UNION SELECT r.source_entity_id FROM relationships r WHERE r.target_entity_id = %s""",
                (eid, eid),
            )
            related_ids = [r[0] for r in cur.fetchall()]
            if related_ids:
                cur.execute(
                    """SELECT DISTINCT em.page_id FROM entity_mentions em
                       WHERE em.entity_id = ANY(%s) LIMIT 30""",
                    (related_ids,),
                )
                for (pid,) in cur.fetchall():
                    if pid not in seen_pages:
                        seen_pages[pid] = {"id": pid, "content": "", "importance": 0.5,
                                           "confidence": 0.5, "age_days": 30, "entities": [], "score": 0.3 * 0.5}
    
    results = sorted(seen_pages.values(), key=lambda x: x["score"], reverse=True)[:limit]
    
    # Fill content for any that were only added via graph traversal
    if results:
        missing = [r for r in results if not r["content"]]
        if missing:
            with conn.cursor() as cur:
                for r in missing:
                    cur.execute("SELECT content FROM pages WHERE id = %s", (r["id"],))
                    row = cur.fetchone()
                    if row:
                        r["content"] = row[0]
    
    return results
