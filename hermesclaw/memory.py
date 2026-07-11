"""Memory capture, search, deduplication, and batch operations."""

import json
import logging
import ollama
from fastapi import HTTPException
from hermesclaw.config import OLLAMA_HOST, OPENROUTER_DEGRADED_MESSAGE
from hermesclaw.db import connect_db, embedding_to_pgvector_literal
from hermesclaw.embeddings import generate_embedding, generate_embeddings
from hermesclaw.scoring import (
    score_memory, normalize_scope_id, normalize_chat_id,
    derive_chat_id, compute_hybrid_score,
)

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
    similar = find_similar_page(capture_text, scope_id=capture_scope_id, chat_id=capture_chat_id)
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
        return {
            "status": "duplicate",
            "page_id": similar["id"],
            "distance": similar["distance"],
            "content": similar["content"],
            "memory_type": meta["memory_type"],
            "score": meta["score"],
        }

    with connect_db() as conn:
        with conn.cursor() as cur:
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
                    capture_text,
                    meta["memory_type"],
                    meta["importance"],
                    meta["confidence"],
                    meta["sentiment"],
                    meta["source"],
                    meta["ttl_days"],
                    capture_scope_id,
                    capture_chat_id,
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
):
    from hermesclaw.embeddings import generate_embedding

    search_scope_id = normalize_scope_id(scope_id)
    search_chat_id = normalize_chat_id(chat_id)

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

    candidates = {}

    with connect_db() as conn:
        with conn.cursor() as cur:
            vector_rows = []
            if qemb_str is not None:
                cur.execute(
                    """
                    SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                           EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0 AS age_days,
                           e.embedding <-> %s::vector AS vector_distance
                    FROM embeddings e
                    JOIN pages p ON p.id = e.page_id
                    WHERE p.is_archived = FALSE
                      AND p.chat_id = %s
                      AND (%s::text IS NULL OR p.scope_id = %s::text)
                    ORDER BY vector_distance
                    LIMIT %s
                    """,
                    (qemb_str, search_chat_id, search_scope_id, search_scope_id, limit * 2),
                )
                vector_rows = cur.fetchall()

            cur.execute(
                """
                SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                       EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0 AS age_days,
                       ts_rank_cd(to_tsvector('english', p.content), plainto_tsquery('english', %s)) AS lexical_rank
                FROM pages p
                WHERE p.is_archived = FALSE
                    AND p.chat_id = %s
                    AND (%s::text IS NULL OR p.scope_id = %s::text)
                  AND to_tsvector('english', p.content) @@ plainto_tsquery('english', %s)
                ORDER BY lexical_rank DESC
                LIMIT %s
                """,
                (query, search_chat_id, search_scope_id, search_scope_id, query, limit * 2),
            )
            lexical_rows = cur.fetchall()

    for r in vector_rows:
        candidates[r[0]] = {
            "id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "age_days": float(r[6] or 0.0),
            "vector_distance": float(r[7]) if r[7] is not None else None,
            "lexical_rank": None,
        }

    for r in lexical_rows:
        existing = candidates.get(r[0])
        row_dict = {
            "id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "age_days": float(r[6] or 0.0),
            "vector_distance": existing.get("vector_distance") if existing else None,
            "lexical_rank": float(r[7]) if r[7] is not None else None,
        }
        if existing:
            existing["lexical_rank"] = float(r[7]) if r[7] is not None else None
        else:
            candidates[r[0]] = row_dict

    results = []
    for item in candidates.values():
        score, explain = compute_hybrid_score(item)
        item["hybrid_score"] = score
        item["explainability"] = explain
        results.append(item)

    results.sort(key=lambda x: x["hybrid_score"], reverse=True)

    if rerank_results:
        results = rerank(query, results)

    ids = [r["id"] for r in results[:limit]]
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
        }
        for r in results[:limit]
    ]


def rerank(query: str, items: list[dict]) -> list[dict]:
    if not items:
        return items
    prompt = (
        f"Query: {query}\n\nRe-rank these items by relevance (0-10 score), "
        f"return JSON: [{{'id': id, 'score': score}}]\n"
    )
    for item in items:
        prompt += f"ID: {item['id']}, Content: {item['content'][:200]}\n"

    resp = client.generate(model="llama3.1:8b", prompt=prompt)
    try:
        ranked = json.loads(resp["response"])
        ranked.sort(key=lambda x: x["score"], reverse=True)
        id_map = {r["id"]: r for r in items}
        return [id_map[r["id"]] for r in ranked if r["id"] in id_map]
    except (json.JSONDecodeError, KeyError, TypeError):
        return items
