"""Memory capture, search, deduplication, and batch operations."""

import json
import logging
import math
import hashlib
import ollama
from fastapi import HTTPException
from hermesclaw.config import OLLAMA_HOST, OPENROUTER_DEGRADED_MESSAGE
from hermesclaw.db import connect_db, embedding_to_pgvector_literal
from hermesclaw.embeddings import generate_embedding, generate_embeddings
from hermesclaw.scoring import (
    score_memory, normalize_scope_id, normalize_chat_id,
    derive_chat_id, compute_hybrid_score, resolve, stability_with_boost,
    ResolutionOp, INTERACTION_BOOST,
)
from hermesclaw.graph import process_memory_for_graph
from hermesclaw.consolidation import store_compressed_version
from hermesclaw.hooks import registry

logger = logging.getLogger("hermesclaw.memory")
client = ollama.Client(host=OLLAMA_HOST)


def find_similar_page(
    text: str,
    threshold: float = 0.05,
    scope_id: str | None = None,
    chat_id: str = "global",
):
    emb = generate_embedding(text)
    emb_str = embedding_to_pgvector_literal(emb)
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.content, e.embedding <-> %s::vector AS dist
                FROM embeddings e
                JOIN pages p ON p.id = e.page_id
                WHERE p.is_archived = FALSE
                  AND p.chat_id = %s
                  AND (%s::text IS NULL OR p.scope_id = %s::text)
                ORDER BY dist ASC
                LIMIT 1
                """,
                (emb_str, normalize_chat_id(chat_id), scope_id, scope_id),
            )
            row = cur.fetchone()
    if row and row[2] <= threshold:
        return {"id": row[0], "content": row[1], "distance": row[2]}
    return None


def _capture_sync(
    text: str | None = None,
    scope_id: str | None = None,
    chat_id: str | None = None,
    body=None,
):
    from hermesclaw.models import CaptureRequest

    capture_text = text if text is not None else (body.text if body else None)
    capture_scope_id = normalize_scope_id(
        scope_id if scope_id is not None else (body.scope_id if body else None)
    )
    capture_chat_id = derive_chat_id(
        chat_id if chat_id is not None else (body.chat_id if body else None),
        capture_scope_id,
    )
    if not capture_text or not capture_text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    capture_text = capture_text.strip()
    meta = score_memory(capture_text)
    initial_stability = {"fact": 2.0, "preference": 1.5, "project": 3.0, "skill": 2.5, "conversation": 1.0}.get(
        meta["memory_type"], 1.0
    )

    # ── Conflict Resolver: fetch nearest neighbors, decide ADD/NOOP/INVALIDATE ──
    emb = None
    try:
        emb = generate_embedding(capture_text)
    except HTTPException:
        pass  # degraded path — skip resolver, always ADD

    if emb is not None:
        neighbors = []
        emb_str = embedding_to_pgvector_literal(emb)
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SET hnsw.ef_search = 200")
                cur.execute(
                    """
                    SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                           e.embedding <=> %s::vector AS dist
                    FROM embeddings e
                    JOIN pages p ON p.id = e.page_id
                    WHERE p.is_archived = FALSE
                      AND p.chat_id = %s
                      AND (%s::text IS NULL OR p.scope_id = %s::text)
                      AND p.valid_to IS NULL
                    ORDER BY dist
                    LIMIT 5
                    """,
                    (emb_str, derive_chat_id(chat_id, capture_scope_id),
                     capture_scope_id, capture_scope_id),
                )
                for row in cur.fetchall():
                    sim = 1.0 / (1.0 + float(row[6]))
                    neighbors.append((sim, {"id": row[0], "content": row[1]}))

        if neighbors:
            op, target_id, reason = resolve(capture_text, neighbors)

            if op == ResolutionOp.NOOP:
                # Reinforce existing memory
                with connect_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """UPDATE pages
                               SET frequency = frequency + 1,
                                   confidence = LEAST(1.0, confidence + 0.02),
                                   stability = %s,
                                   last_used = NOW(),
                                   last_access = NOW(),
                                   updated_at = NOW()
                               WHERE id = %s""",
                            (stability_with_boost(1.0, 1, "reinforce"), target_id),
                        )
                        conn.commit()
                return {
                    "status": "duplicate",
                    "page_id": target_id,
                    "reason": reason,
                    "score": meta["score"],
                }

            if op == ResolutionOp.INVALIDATE:
                # Close old fact, then insert new as superseding
                with connect_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO pages (
                                content, memory_type, importance, confidence, frequency,
                                sentiment, source, ttl_days, scope_id, chat_id,
                                stability, content_hash, last_access, updated_at, last_used
                            ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
                            RETURNING id""",
                            (
                                capture_text, meta["memory_type"],
                                meta["importance"], meta["confidence"],
                                meta["sentiment"], meta["source"], meta["ttl_days"],
                                capture_scope_id, derive_chat_id(chat_id, capture_scope_id),
                                initial_stability, hashlib.sha256(capture_text.encode()).hexdigest(),
                            ),
                        )
                        new_id = cur.fetchone()[0]
                        cur.execute(
                            """UPDATE pages SET valid_to = NOW(), superseded_by = %s
                               WHERE id = %s""",
                            (new_id, target_id),
                        )
                        conn.commit()
                page_id = new_id
                # Still store the embedding below
                try:
                    emb_str = embedding_to_pgvector_literal(emb)
                    with connect_db() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)",
                                (page_id, emb_str),
                            )
                            conn.commit()
                except Exception:
                    pass
                return {
                    "status": "superseded",
                    "page_id": page_id,
                    "supersedes": target_id,
                    "reason": reason,
                    "score": meta["score"],
                }

    # ── ADD: insert new memory with initial stability ──
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO pages (
                    content, memory_type, importance, confidence, frequency,
                    sentiment, source, ttl_days, scope_id, chat_id,
                    stability, content_hash, last_access, updated_at, last_used
                ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
                RETURNING id""",
                (
                    capture_text, meta["memory_type"],
                    meta["importance"], meta["confidence"],
                    meta["sentiment"], meta["source"], meta["ttl_days"],
                    capture_scope_id, derive_chat_id(chat_id, capture_scope_id),
                    initial_stability, hashlib.sha256(capture_text.encode()).hexdigest(),
                ),
            )
            page_id = cur.fetchone()[0]
            conn.commit()

    try:
        emb = generate_embedding(capture_text)
        emb_str = embedding_to_pgvector_literal(emb)
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)",
                    (page_id, emb_str),
                )
                conn.commit()
    except HTTPException as ex:
        if ex.status_code == 503 and ex.detail == OPENROUTER_DEGRADED_MESSAGE:
            return {
                "status": "ok_degraded",
                "page_id": page_id,
                "memory_type": meta["memory_type"],
                "score": meta["score"],
                "importance": round(meta["importance"], 3),
                "confidence": round(meta["confidence"], 3),
                "warning": OPENROUTER_DEGRADED_MESSAGE,
            }
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pages WHERE id = %s", (page_id,))
                conn.commit()
        raise ex
    except Exception as ex:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pages WHERE id = %s", (page_id,))
                conn.commit()
        raise HTTPException(status_code=500, detail=f"Failed to store embedding: {ex}") from ex

    return {
        "status": "ok",
        "page_id": page_id,
        "memory_type": meta["memory_type"],
        "score": meta["score"],
        "importance": round(meta["importance"], 3),
        "confidence": round(meta["confidence"], 3),
    }


def _capture_with_graph(
    text: str | None = None,
    scope_id: str | None = None,
    chat_id: str | None = None,
    body=None,
):
    """Capture memory with LLM fact extraction + entity extraction. 
    Only stores if LLM identifies it as a meaningful fact (not small talk).
    """
    capture_text = text if text is not None else (body.text if body else None)
    if not capture_text or not capture_text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    capture_scope_id = scope_id
    capture_chat_id = chat_id

    # LLM fact check
    try:
        fact_check = _llm_fact_check(capture_text)
        if not fact_check.get("is_fact", True):
            logger.debug("LLM fact check skipped non-fact: %s", capture_text[:80])
            return {
                "status": "skipped",
                "reason": fact_check.get("reason", "not a meaningful fact"),
                "text": capture_text[:200],
            }
        if fact_check.get("summary"):
            capture_text = fact_check["summary"]
    except Exception as ex:
        logger.debug("LLM fact check failed (non-fatal, storing raw): %s", ex)

    # beforeSave hook
    hook_result = registry.run("beforeSave", capture_text, scope_id=capture_scope_id, chat_id=capture_chat_id)
    if hook_result is None:
        return {"status": "skipped", "reason": "blocked by beforeSave hook"}
    if isinstance(hook_result, tuple):
        capture_text, capture_scope_id, capture_chat_id = hook_result[0], hook_result[1] or capture_scope_id, hook_result[2] or capture_chat_id

    result = _capture_sync(capture_text, capture_scope_id, capture_chat_id, body)
    if result.get("status") in ("ok", "ok_degraded") and "page_id" in result:
        page_id = result["page_id"]
        try:
            with connect_db() as conn:
                process_memory_for_graph(conn, capture_text, page_id, use_llm=True, llm_generate=client.generate)
                store_compressed_version(conn, page_id, capture_text)
                conn.commit()
        except Exception as ex:
            logger.warning("Graph/consolidation post-capture failed (non-fatal): %s", ex)
        result["graph_processed"] = True
        # afterSave hook
        try:
            registry.run("afterSave", result.get("page_id"), text=capture_text, scope_id=capture_scope_id)
        except Exception as ex:
            logger.debug("afterSave hook error (non-fatal): %s", ex)
    return result


def _llm_fact_check(text: str) -> dict:
    """Use Ollama to check if text contains a meaningful fact worth storing.
    Returns dict with is_fact, summary, reason, confidence.
    """
    prompt = (
        "Determine if the following text contains a meaningful, storable fact "
        "(a preference, project detail, decision, instruction, or personal info).\n"
        "If YES: return {\"is_fact\": true, \"summary\": \"<concise 1-sentence summary>\", \"type\": \"<preference|project|fact|instruction|personal>\", \"confidence\": <0-1>}\n"
        "If NO (small talk, greeting, vague statement): return {\"is_fact\": false, \"reason\": \"<why>\"}\n"
        "Return ONLY valid JSON, no other text.\n\n"
        f"Text: {text[:1500]}"
    )
    resp = client.generate(model="llama3.1:8b", prompt=prompt)
    raw = resp["response"].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("\n", 1)[0]
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            return {"is_fact": False, "reason": "invalid LLM response"}
        return result
    except (json.JSONDecodeError, KeyError):
        return {"is_fact": True}


def _capture_batch_sync(body):
    if not body.items:
        raise HTTPException(status_code=400, detail="items are required")

    prepared = []
    for item in body.items:
        text = (item.text or "").strip()
        if not text:
            continue
        meta = score_memory(text)
        normalized_scope_id = normalize_scope_id(item.scope_id)
        prepared.append((
            item.msg_id,
            normalized_scope_id,
            derive_chat_id(item.chat_id, normalized_scope_id),
            text,
            meta,
        ))

    if not prepared:
        raise HTTPException(status_code=400, detail="no valid non-empty items")

    if body.skip_dedupe:
        dedupe_candidates = prepared
    else:
        dedupe_candidates = []
        for msg_id, scope_id, chat_id, text, meta in prepared:
            similar = find_similar_page(text, scope_id=scope_id, chat_id=chat_id)
            if similar:
                with connect_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE pages
                            SET frequency = frequency + 1,
                                confidence = LEAST(1.0, confidence + 0.01),
                                last_used = NOW(),
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (similar["id"],),
                        )
                        conn.commit()
                continue
            dedupe_candidates.append((msg_id, scope_id, chat_id, text, meta))

    if not dedupe_candidates:
        return {"status": "ok", "processed": 0, "msg_ids": []}

    texts = [x[3] for x in dedupe_candidates]
    embeddings = generate_embeddings(texts)
    if len(embeddings) != len(texts):
        raise HTTPException(status_code=500, detail="embedding batch size mismatch")

    inserted_msg_ids = []
    with connect_db() as conn:
        with conn.cursor() as cur:
            page_ids = []
            for msg_id, scope_id, chat_id, text, meta in dedupe_candidates:
                cur.execute(
                    """
                    INSERT INTO pages (
                        content, memory_type, importance, confidence, frequency,
                        sentiment, source, ttl_days, scope_id, chat_id, updated_at, last_used
                    )
                    VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING id
                    """,
                    (
                        text,
                        meta["memory_type"],
                        meta["importance"],
                        meta["confidence"],
                        meta["sentiment"],
                        meta["source"],
                        meta["ttl_days"],
                        scope_id,
                        chat_id,
                    ),
                )
                page_ids.append(cur.fetchone()[0])
                if msg_id is not None:
                    inserted_msg_ids.append(int(msg_id))

            for page_id, embedding in zip(page_ids, embeddings):
                emb_str = embedding_to_pgvector_literal(embedding)
                cur.execute(
                    "INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)",
                    (page_id, emb_str),
                )
            conn.commit()

    return {"status": "ok", "processed": len(dedupe_candidates), "msg_ids": inserted_msg_ids}


def _search_sync(
    query: str = "",
    limit: int = 5,
    rerank_results: bool = False,
    scope_id: str | None = None,
    chat_id: str = "global",
    search_type: str = "hybrid",
    days_back: int | None = None,
):
    from hermesclaw.embeddings import generate_embedding

    search_scope_id = normalize_scope_id(scope_id)
    search_chat_id = normalize_chat_id(chat_id)

    # Temporal filter
    days_filter = ""
    days_params: list = []
    if days_back is not None and days_back > 0:
        days_filter = f" AND COALESCE(p.last_used, p.created_at) >= NOW() - INTERVAL '{days_back} days'"

    # ── HNSW ef_search per query type ──
    ef_search = {"exact": 400, "high_recall": 200, "hybrid": 80, "vector": 40}.get(search_type, 80)

    if query.strip() == "":
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, memory_type, importance, confidence, frequency
                    FROM pages
                    WHERE is_archived = FALSE
                      AND chat_id = %s
                      AND (%s::text IS NULL OR scope_id = %s::text)
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (search_chat_id, search_scope_id, search_scope_id, limit),
                )
                rows = cur.fetchall()
        ids = [r[0] for r in rows]
        if ids:
            with connect_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE pages SET last_retrieved = NOW(), frequency = frequency + 1 WHERE id = ANY(%s)",
                        (ids,),
                    )
                    conn.commit()
        return [
            {
                "id": r[0],
                "content": r[1],
                "memory_type": r[2],
                "importance": r[3],
                "confidence": r[4],
                "frequency": r[5],
                "hybrid_score": None,
                "explainability": {
                    "reasons": ["recent memories list"],
                    "components": {},
                },
            }
            for r in rows
        ]

    qemb_str = None
    degraded_search = False
    degraded_reason = None
    try:
        qemb = generate_embedding(query)
        qemb_str = embedding_to_pgvector_literal(qemb)
    except HTTPException as ex:
        if ex.status_code == 503 and ex.detail == OPENROUTER_DEGRADED_MESSAGE:
            degraded_search = True
            degraded_reason = OPENROUTER_DEGRADED_MESSAGE
        else:
            raise

    # ── Phase D: SQL-hybrid-score search (CTE + inline score + Ebbinghaus retention) ──
    rows = []
    with connect_db() as conn:
        with conn.cursor() as cur:
            if qemb_str is None:
                cur.execute(
                    f"""
                    SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                           p.stability,
                           EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_access, p.last_used, p.created_at))) / 86400.0 AS access_age_days,
                           EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0 AS age_days,
                           NULL::float8 AS vec_dist,
                           ts_rank_cd(to_tsvector('english', p.content), plainto_tsquery('english', %s)) AS lex_rank,
                           COALESCE(exp(-EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_access, p.last_used, p.created_at))) / 86400.0 / GREATEST(p.stability, 0.01)), 0.0) AS retention,
                           (0.15 * COALESCE(ts_rank_cd(to_tsvector('english', p.content), plainto_tsquery('english', %s)) / (ts_rank_cd(to_tsvector('english', p.content), plainto_tsquery('english', %s)) + 1.0), 0.0)
                            + 0.15 * COALESCE(exp(-EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_access, p.last_used, p.created_at))) / 86400.0 / GREATEST(p.stability, 0.01)), 0.0)
                            + 0.12 * p.importance
                            + 0.10 * COALESCE(exp(-GREATEST(0.0, EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0) / 30.0), 0.5)
                            + 0.08 * COALESCE(log(1.0 + LEAST(p.frequency::numeric, 1000)) / log(101), 0.0))
                           * (0.5 + 0.5 * p.confidence) AS hybrid_score
                    FROM pages p
                    WHERE p.is_archived = FALSE
                      AND p.chat_id = %s
                      AND (%s::text IS NULL OR p.scope_id = %s::text)
                      {days_filter}
                      AND to_tsvector('english', p.content) @@ plainto_tsquery('english', %s)
                    ORDER BY hybrid_score DESC
                    LIMIT %s
                    """,
                    (query, query, query, search_chat_id, search_scope_id, search_scope_id, query, limit * 2),
                )
            else:
                cur.execute(f"SET hnsw.ef_search = {ef_search}")
                cur.execute(
                    f"""
                    WITH vector_hits AS (
                        SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                               p.stability,
                               EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_access, p.last_used, p.created_at))) / 86400.0 AS access_age_days,
                               EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0 AS age_days,
                               e.embedding <-> %s::vector AS vec_dist,
                               NULL::float8 AS lex_rank,
                               COALESCE(exp(-EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_access, p.last_used, p.created_at))) / 86400.0 / GREATEST(p.stability, 0.01)), 0.0) AS retention
                        FROM embeddings e
                        JOIN pages p ON p.id = e.page_id
                        WHERE p.is_archived = FALSE
                          AND p.chat_id = %s
                          AND (%s::text IS NULL OR p.scope_id = %s::text)
                          {days_filter}
                        ORDER BY vec_dist
                        LIMIT %s
                    ),
                    lexical_hits AS (
                        SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                               p.stability,
                               EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_access, p.last_used, p.created_at))) / 86400.0 AS access_age_days,
                               EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0 AS age_days,
                               NULL::float8 AS vec_dist,
                               ts_rank_cd(to_tsvector('english', p.content), plainto_tsquery('english', %s)) AS lex_rank,
                               COALESCE(exp(-EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_access, p.last_used, p.created_at))) / 86400.0 / GREATEST(p.stability, 0.01)), 0.0) AS retention
                        FROM pages p
                        WHERE p.is_archived = FALSE
                          AND p.chat_id = %s
                          AND (%s::text IS NULL OR p.scope_id = %s::text)
                          {days_filter}
                          AND to_tsvector('english', p.content) @@ plainto_tsquery('english', %s)
                        ORDER BY lex_rank DESC
                        LIMIT %s
                    ),
                    combined AS (
                        SELECT * FROM vector_hits
                        UNION ALL
                        SELECT v.* FROM lexical_hits v
                        WHERE NOT EXISTS (SELECT 1 FROM vector_hits vv WHERE vv.id = v.id)
                    )
                    SELECT id, content, memory_type, importance, confidence, frequency, stability,
                           access_age_days, age_days, vec_dist, lex_rank, retention,
                           (0.30 * COALESCE(1.0 / (1.0 + vec_dist), 0.0)
                            + 0.15 * COALESCE(lex_rank / (lex_rank + 1.0), 0.0)
                            + 0.15 * retention
                            + 0.12 * importance
                            + 0.10 * COALESCE(exp(-GREATEST(0.0, age_days) / 30.0), 0.5)
                            + 0.08 * COALESCE(log(1.0 + LEAST(frequency::numeric, 1000)) / log(101), 0.0))
                           * (0.5 + 0.5 * confidence) AS hybrid_score
                    FROM combined
                    ORDER BY hybrid_score DESC
                    LIMIT %s
                    """,
                    (
                        qemb_str, search_chat_id, search_scope_id, search_scope_id,
                        *days_params, limit * 2,
                        query, search_chat_id, search_scope_id, search_scope_id,
                        query, limit * 2,
                        limit,
                    ),
                )
            rows = cur.fetchall()

    # Reconstruct explainability from SQL results (with Ebbinghaus retention)
    results = []
    for r in rows:
        stability_val = float(r[6] or 1.0)
        access_age_days = float(r[7] or 0.0)
        age_days = float(r[8] or 0.0)
        vec_dist = float(r[9]) if r[9] is not None else None
        lex_rank_val = float(r[10]) if r[10] is not None else None
        retention_val = float(r[11]) if r[11] is not None else 1.0
        importance = float(r[3] or 0.5)
        confidence = float(r[4] or 0.5)
        frequency = int(r[5] or 1)

        vec_norm = 1.0 / (1.0 + vec_dist) if vec_dist is not None else 0.0
        lex_norm = lex_rank_val / (lex_rank_val + 1.0) if lex_rank_val is not None else 0.0
        recency_norm = math.exp(-max(0.0, age_days) / 30.0)
        freq_norm = math.log10(1 + min(frequency, 1000)) / math.log10(101)
        staleness_norm = 0.0  # not fetched in search; future: valid_to check

        explain = {
            "components": {
                "vector": round(vec_norm, 4),
                "lexical": round(lex_norm, 4),
                "retention": round(retention_val, 4),
                "importance": round(importance, 4),
                "confidence": round(confidence, 4),
                "recency": round(recency_norm, 4),
                "frequency": round(freq_norm, 4),
                "staleness": round(staleness_norm, 4),
            },
            "weights": {
                "vector": 0.30,
                "lexical": 0.15,
                "retention": 0.15,
                "importance": 0.12,
                "recency": 0.10,
                "frequency": 0.08,
                "staleness_penalty": -0.10,
            },
        }
        reasons = []
        if lex_norm >= 0.35:
            reasons.append("strong keyword overlap")
        if vec_norm >= 0.6:
            reasons.append("high semantic similarity")
        if retention_val >= 0.7:
            reasons.append("fresh in memory (Ebbinghaus)")
        if importance >= 0.75:
            reasons.append("high importance memory")
        if recency_norm >= 0.6:
            reasons.append("recently used")
        if freq_norm >= 0.5:
            reasons.append("frequently retrieved")
        explain["reasons"] = reasons or ["balanced hybrid match"]
        explain["final_score"] = float(r[12]) if len(r) > 12 else 0.0

        results.append({
            "id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": importance,
            "confidence": confidence,
            "frequency": frequency,
            "stability": stability_val,
            "retention": retention_val,
            "hybrid_score": float(r[12]) if len(r) > 12 else 0.0,
            "vector_distance": vec_dist,
            "lexical_rank": lex_rank_val,
            "age_days": round(age_days, 2),
            "explainability": explain,
        })

    if rerank_results:
        results = rerank(query, results)

    ids = [row["id"] for row in results[:limit]]
    if ids:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE pages
                       SET last_retrieved = NOW(), last_access = NOW(),
                           frequency = frequency + 1,
                           stability = GREATEST(stability, %s)
                       WHERE id = ANY(%s)""",
                    (stability_with_boost(1.0, 1, "retrieve"), ids),
                )
                conn.commit()

    return [{
        "id": r["id"],
        "content": r["content"],
        "memory_type": r["memory_type"],
        "importance": r["importance"],
        "confidence": r["confidence"],
        "frequency": r["frequency"],
        "hybrid_score": r["hybrid_score"],
        "vector_distance": r.get("vector_distance"),
        "lexical_rank": r.get("lexical_rank"),
        "age_days": round(float(r.get("age_days") or 0.0), 2),
        "explainability": r["explainability"],
        "degraded": degraded_search,
        "degraded_reason": degraded_reason,
    } for r in results[:limit]]


def rerank(query: str, items: list[dict]) -> list[dict]:
    if not items:
        return items
    
    # Find available LLM model
    model = None
    llm_client = None
    for host in ["http://host.docker.internal:11434", OLLAMA_HOST or "http://host.docker.internal:11435"]:
        try:
            c = ollama.Client(host=host)
            tags = c.list()
            for m in tags.models:
                if "embed" not in (m.model or "") and "llama" in (m.model or "").lower():
                    model, llm_client = m.model, c
                    break
            if not model:
                for m in tags.models:
                    if "embed" not in (m.model or ""):
                        model, llm_client = m.model, c
                        break
            if model:
                break
        except Exception:
            continue
    
    if not model or not llm_client:
        return items

    prompt = (
        f"Query: {query}\n\nRe-rank these items by relevance (0-10 score), "
        f"return JSON: [{{'id': id, 'score': score}}]\n"
    )
    for item in items:
        prompt += f"ID: {item['id']}, Content: {item['content'][:200]}\n"

    try:
        resp = llm_client.generate(model=model, prompt=prompt)
        ranked = json.loads(resp["response"])
        ranked.sort(key=lambda x: x["score"], reverse=True)
        id_map = {r["id"]: r for r in items}
        return [id_map[r["id"]] for r in ranked if r["id"] in id_map]
    except (json.JSONDecodeError, KeyError, TypeError):
        return items
