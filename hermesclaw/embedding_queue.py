"""Async embedding queue — capture returns immediately, embedding processed in background.

At 2M scale, synchronous embedding on every capture blocks the API. This module:
1. Enqueues embedding jobs after INSERT
2. Processes embeddings in a background worker thread (batched)
3. Falls back to synchronous if queue is empty (cold start)
"""
import json
import logging
import os
import queue
import threading
import time

from hermesclaw.db import connect_db, embedding_to_pgvector_literal
from hermesclaw.embeddings import generate_embedding

logger = logging.getLogger("hermesclaw.embedding_queue")

# ── Queue ──
_job_queue: queue.Queue = queue.Queue(maxsize=5000)
_worker_started = False
_worker_lock = threading.Lock()
_shutdown = threading.Event()

# ── Public API ──


def enqueue(page_id: int, text: str) -> None:
    """Non-blocking: push embedding job, worker processes it."""
    try:
        _job_queue.put_nowait({"page_id": page_id, "text": text, "retry": 0})
    except queue.Full:
        logger.warning("[EMBED-Q] Queue full (%d), falling back to sync", _job_queue.qsize())
        _process_sync(page_id, text)


def ensure_worker() -> None:
    """Start background worker thread (idempotent)."""
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        t = threading.Thread(target=_worker_loop, daemon=True, name="embed-worker")
        t.start()
        logger.info("[EMBED-Q] Worker thread started")


def queue_size() -> int:
    return _job_queue.qsize()


# ── Worker ──

_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "10"))
_BATCH_INTERVAL = float(os.getenv("EMBED_BATCH_INTERVAL", "0.5"))
_MAX_RETRIES = 3


def _worker_loop() -> None:
    """Process embedding jobs in batches for throughput."""
    while not _shutdown.is_set():
        batch = []
        # Collect up to BATCH_SIZE jobs (with timeout)
        try:
            job = _job_queue.get(timeout=1.0)
            batch.append(job)
        except queue.Empty:
            continue

        while len(batch) < _BATCH_SIZE:
            try:
                batch.append(_job_queue.get_nowait())
            except queue.Empty:
                break

        # Process batch
        for job in batch:
            if _shutdown.is_set():
                return
            try:
                emb = generate_embedding(job["text"])
                _store_embedding(job["page_id"], emb)
                logger.debug("[EMBED-Q] page_id=%s ok", job["page_id"])
            except Exception as ex:
                retry = job.get("retry", 0) + 1
                if retry <= _MAX_RETRIES:
                    logger.warning("[EMBED-Q] page_id=%s retry %d/%d: %s",
                                   job["page_id"], retry, _MAX_RETRIES, ex)
                    _job_queue.put({"page_id": job["page_id"], "text": job["text"], "retry": retry})
                else:
                    logger.error("[EMBED-Q] page_id=%s failed after %d retries: %s",
                                 job["page_id"], _MAX_RETRIES, ex)

        # Small inter-batch pause to let other threads breathe
        if not _shutdown.is_set() and not _job_queue.empty():
            time.sleep(_BATCH_INTERVAL)


def _store_embedding(page_id: int, embedding: list[float]) -> None:
    """Store embedding vector in the embeddings table."""
    emb_str = embedding_to_pgvector_literal(embedding)
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM embeddings WHERE page_id = %s", (page_id,)
            )
            cur.execute(
                "INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)",
                (page_id, emb_str),
            )
            conn.commit()


def _process_sync(page_id: int, text: str) -> None:
    """Fallback: generate embedding synchronously (cold start / queue full)."""
    try:
        emb = generate_embedding(text)
        _store_embedding(page_id, emb)
    except Exception as ex:
        logger.error("[EMBED-Q] Sync fallback failed for page_id=%s: %s", page_id, ex)


def shutdown() -> None:
    _shutdown.set()
