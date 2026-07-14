"""All route handlers."""

import math
import time
import html
import json
import os
import logging
import threading
from fastapi import APIRouter, HTTPException, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from hermesclaw.routes._shared import _jinja_env, _dashboard_redirect, safe_int, safe_float
from hermesclaw.config import (
    API_KEY, DASHBOARD_SCOPE_ALL, DASHBOARD_SCOPE_UNSCOPED,
    SCOPE_ALIASES, AUTO_UPDATE_ENABLED, AUTO_UPDATE_APPLY,
    AUTO_UPDATE_INTERVAL_SECONDS,
)
from hermesclaw.auth import (
    get_current_username, require_api_key, WATCHDOG_STATUS, SYNC_LIVENESS,
)
from hermesclaw.db import connect_db, ensure_phase1_schema, cleanup_orphaned_embeddings
from hermesclaw.models import CaptureRequest, BatchCaptureRequest, ArchiveSelectionRequest, WatchdogStatusRequest
from hermesclaw.memory import _capture_sync, _capture_with_graph, _capture_batch_sync, _search_sync
from hermesclaw.scoring import clamp, normalize_scope_id, build_scope_filter, format_scope_label
from hermesclaw.embeddings import provider_runtime_info
from hermesclaw.optimizer import (
    run_decay_and_archive_once, run_tier_assignment, get_optimizer_review, get_optimizer_dry_run,
    archive_selected_pages, get_latest_manual_archive_batch_id, restore_archive_batch,
)
from hermesclaw.importer import import_hermes_sessions
from hermesclaw.graph import query_entity_graph, get_memories_for_entity, get_top_entities, graph_rag_search
from hermesclaw.dedup import find_and_merge_duplicates
from hermesclaw.reflection import analyze_memories
from hermesclaw.episodic import ensure_episodic_schema, record_episode, get_timeline
from hermesclaw.update import get_update_status, run_update, get_version_info
from hermesclaw.ask import ask_question
from hermesclaw.export import export_memories

logger = logging.getLogger("hermesclaw.routes")

router = APIRouter()

_CHANGE_SECRET = threading.Event()
_shutdown_event = threading.Event()


# ---------------------------------------------------------------------------
# Public endpoints (API key or exempt)
# ---------------------------------------------------------------------------
@router.get("/")
async def root_redirect():
    return RedirectResponse(url="/dashboard", status_code=307)


@router.get("/healthz")
async def healthz():
    db_ok = True
    db_error = ""
    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as ex:
        db_ok = False
        db_error = str(ex)

    sync_snapshot = {
        "running": bool(SYNC_LIVENESS.get("running")),
        "last_success_ts": SYNC_LIVENESS.get("last_success_ts"),
        "last_error_ts": SYNC_LIVENESS.get("last_error_ts"),
        "last_error": SYNC_LIVENESS.get("last_error"),
        "restart_count": int(SYNC_LIVENESS.get("restart_count") or 0),
    }

    sync_ingest = None
    try:
        import memory_sync
        if hasattr(memory_sync, "LIVENESS"):
            sync_ingest = dict(memory_sync.LIVENESS)
    except Exception:
        sync_ingest = None

    status_text = "ok" if db_ok else "degraded"
    payload = {
        "status": status_text,
        "database": "ok" if db_ok else "error",
        "sync": {"worker": sync_snapshot, "ingest": sync_ingest},
    }
    if db_error:
        payload["database_error"] = db_error
    return JSONResponse(payload, status_code=200 if db_ok else 503)


@router.post("/capture")
async def capture(
    text: str | None = None,
    scope_id: str | None = None,
    chat_id: str | None = None,
    body: CaptureRequest | None = None,
):
    return await run_in_threadpool(_capture_with_graph, text, scope_id, chat_id, body)


@router.post("/capture/batch", dependencies=[Depends(require_api_key)])
async def capture_batch(body: BatchCaptureRequest):
    return await run_in_threadpool(_capture_batch_sync, body)


@router.post("/watchdog/status", dependencies=[Depends(require_api_key)])
async def watchdog_status_update(body: WatchdogStatusRequest):
    WATCHDOG_STATUS["pending"] = max(0, int(body.pending))
    WATCHDOG_STATUS["last_synced_id"] = int(body.last_synced_id)
    WATCHDOG_STATUS["latest_source_id"] = int(body.latest_source_id)
    WATCHDOG_STATUS["last_error"] = (body.last_error or "").strip() or None
    WATCHDOG_STATUS["updated_at"] = int(time.time())
    return {"status": "ok"}


@router.get("/search")
async def search(
    query: str = "",
    limit: int = 5,
    rerank_results: bool = False,
    scope_id: str | None = None,
    chat_id: str = "global",
    search_type: str = "hybrid",
    days_back: int | None = None,
):
    return await run_in_threadpool(_search_sync, query, limit, rerank_results, scope_id, chat_id, search_type, days_back)


@router.get("/version")
async def version_info():
    return {
        "status": "ok",
        "version": get_version_info(),
        "providers": provider_runtime_info(),
    }


# ---------------------------------------------------------------------------
# Protected endpoints (dashboard auth)
# ---------------------------------------------------------------------------
@router.post("/delete", dependencies=[Depends(get_current_username)])
async def delete_page(page_id: int = Form(...)):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pages WHERE id = %s", (page_id,))
            conn.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/export", dependencies=[Depends(get_current_username)])
async def export_data(format: str = "json", scope_id: str | None = None,
                      include_archived: bool = False, include_graph: bool = False):
    """Export all memories as JSON (default) or Markdown. Supports scope filter, graph entities."""
    from fastapi.responses import JSONResponse, PlainTextResponse
    result = await run_in_threadpool(lambda: export_memories(
        format=format, scope_id=scope_id, include_archived=include_archived, include_graph=include_graph))
    if format == "markdown":
        return PlainTextResponse(result, media_type="text/markdown",
                                 headers={"Content-Disposition": "attachment; filename=hermesclaw-export.md"})
    return JSONResponse(result, headers={"Content-Disposition": "attachment; filename=hermesclaw-export.json"})


@router.post("/tag_auto/{page_id}", dependencies=[Depends(get_current_username)])
async def tag_auto(page_id: int):
    import ollama
    from hermesclaw.config import OLLAMA_HOST
    client = ollama.Client(host=OLLAMA_HOST)

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    prompt = f"Analyze: '{row[0][:500]}'. Provide 3 comma-separated relevant tags. Only output the tags."
    res = client.generate(model="llama3.1:8b", prompt=prompt)
    tags = res["response"].replace(" ", "").split(",")

    for tag in tags:
        if tag:
            with connect_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO tags (page_id, tag) VALUES (%s, %s)", (page_id, tag))
                    conn.commit()
    return {"status": "ok", "tags": tags}


@router.get("/page_html", response_class=HTMLResponse, dependencies=[Depends(get_current_username)])
async def view_page_html(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()
    if not row:
        return "<h1>Not found</h1>"
    content = html.escape(row[0])
    return (
        f"<html><head><title>Page {page_id}</title></head>"
        f"<body><h1>Page {page_id}</h1><pre>{content}</pre>"
        f"<br><a href='/dashboard'>Back to Dashboard</a></body></html>"
    )


# ---------------------------------------------------------------------------
# Dashboard (Jinja2 template)
# ---------------------------------------------------------------------------
@router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(get_current_username)])
async def dashboard(
    query: str | None = None,
    selected_scope: str = DASHBOARD_SCOPE_ALL,
    before_id: int | None = None,
    memory_type: str | None = None,
    days_back: int | None = None,
    optimizer_msg: str | None = None,
    dry_run_msg: str | None = None,
    restore_msg: str | None = None,
    health_stale_days: int = 14,
    health_confidence_threshold: float = 0.3,
    health_limit: int = 8,
):
    per_page = 20
    safe_health_stale_days = max(1, min(health_stale_days, 3650))
    safe_health_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_health_limit = max(1, min(health_limit, 50))
    active_scope = (selected_scope or DASHBOARD_SCOPE_ALL).strip() or DASHBOARD_SCOPE_ALL
    if active_scope != DASHBOARD_SCOPE_UNSCOPED:
        normalized_scope = normalize_scope_id(active_scope)
        active_scope = normalized_scope if normalized_scope is not None else DASHBOARD_SCOPE_ALL

    list_scope_clause, list_scope_params = build_scope_filter(active_scope, "scope_id")
    try:
        ensure_phase1_schema()

        review = get_optimizer_review(
            limit=safe_health_limit, stale_days=safe_health_stale_days,
            confidence_threshold=safe_health_confidence, selected_scope=active_scope,
        )
        dry_run = get_optimizer_dry_run(
            limit=safe_health_limit, stale_days=safe_health_stale_days,
            confidence_threshold=safe_health_confidence, selected_scope=active_scope,
        )
        latest_manual_batch_id = get_latest_manual_archive_batch_id()
        version_info_val = get_version_info()
        update_status_val = get_update_status(fetch_remote=False)

        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT scope_id, COUNT(*)
                    FROM pages
                    WHERE scope_id IS NOT NULL
                    GROUP BY scope_id
                    ORDER BY COUNT(*) DESC, scope_id ASC
                    LIMIT 200
                    """
                )
                scope_rows = cur.fetchall()

                cur.execute(
                    f"SELECT COUNT(*) FROM pages WHERE 1=1{list_scope_clause}",
                    list_scope_params,
                )
                selected_scope_total_count = int(cur.fetchone()[0] or 0)

                if query:
                    cur.execute(
                        f"SELECT id, content FROM pages WHERE content ILIKE %s{list_scope_clause} {'AND id < %s' if before_id else ''} ORDER BY id DESC LIMIT %s",
                        [f"%{query}%"] + list_scope_params + ([before_id] if before_id else []) + [per_page + 1],
                    )
                    rows = cur.fetchall()
                    cur.execute(
                        f"SELECT COUNT(*) FROM pages WHERE content ILIKE %s{list_scope_clause}",
                        [f"%{query}%"] + list_scope_params,
                    )
                    total_items = cur.fetchone()[0]
                else:
                    cur.execute(
                        f"SELECT id, content FROM pages WHERE 1=1{list_scope_clause} {'AND id < %s' if before_id else ''} ORDER BY id DESC LIMIT %s",
                        list_scope_params + ([before_id] if before_id else []) + [per_page + 1],
                    )
                    rows = cur.fetchall()
                    cur.execute(
                        f"SELECT COUNT(*) FROM pages WHERE 1=1{list_scope_clause}",
                        list_scope_params,
                    )
                    total_items = cur.fetchone()[0]

        # ── Keyset cursor: fetch per_page+1, detect overflow ──
        has_next = len(rows) > per_page
        if has_next:
            rows = rows[:per_page]
        next_cursor_id = rows[-1][0] if rows else None
        prev_cursor_id = before_id  # used to construct "previous page" link
    except Exception as ex:
        return HTMLResponse(
            f"""
            <html><head><title>Dashboard Error</title></head>
            <body style='font-family:sans-serif;background:#121212;color:#fff;padding:24px;'>
                <h1>Dashboard Error</h1>
                <p>The dashboard failed to load due to a backend/runtime issue.</p>
                <pre style='white-space:pre-wrap;background:#1e1e1e;border:1px solid #333;padding:12px;border-radius:6px;'>{html.escape(str(ex))}</pre>
            </body></html>
            """,
            status_code=500,
        )

    # Build template data
    watchdog_pending_text = "n/a"
    watchdog_updated_text = "unknown"
    watchdog_pending_value = WATCHDOG_STATUS.get("pending")
    watchdog_last_synced_id = WATCHDOG_STATUS.get("last_synced_id")
    watchdog_latest_source_id = WATCHDOG_STATUS.get("latest_source_id")
    watchdog_last_error = WATCHDOG_STATUS.get("last_error")

    if WATCHDOG_STATUS.get("pending") is not None:
        watchdog_pending_text = str(WATCHDOG_STATUS["pending"])
    if WATCHDOG_STATUS.get("updated_at") is not None:
        age_seconds = max(0, int(time.time()) - int(WATCHDOG_STATUS["updated_at"]))
        watchdog_updated_text = f"{age_seconds}s ago"

    scope_options = [
        (DASHBOARD_SCOPE_ALL, "All users/scopes"),
        (DASHBOARD_SCOPE_UNSCOPED, "Unscoped (legacy rows)"),
    ]
    for sid, count in scope_rows:
        scope_options.append((str(sid), format_scope_label(str(sid), int(count))))

    if active_scope not in {o[0] for o in scope_options}:
        scope_options.append((active_scope, f"{format_scope_label(active_scope)} (selected)"))

    # Galaxy view data
    galaxy_tenants_list = [
        {"name": format_scope_label(str(sid)), "count": int(c), "scope": str(sid)}
        for sid, c in scope_rows[:10]
    ]
    galaxy_total = int(total_items)

    # 2M-scale: use materialized view for dashboard aggregates
    mv_stats = None
    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT total, active, hot, warm, standard, cold, high_conf, med_conf, low_conf FROM pages_stats_mv")
                mv_stats = cur.fetchone()
    except Exception:
        logger.warning("dashboard: MV read failed, using fallback", exc_info=True)
        mv_stats = None

    if mv_stats:
        galaxy_high_conf = int(mv_stats[6] or 0)
        galaxy_med_conf = int(mv_stats[7] or 0)
        galaxy_low_conf = int(mv_stats[8] or 0)
        tier_stats = {"hot": mv_stats[2] or 0, "warm": mv_stats[3] or 0,
                      "standard": mv_stats[4] or 0, "cold": mv_stats[5] or 0}
        galaxy_type_data = {}
        # Use scope MV for tenant list
        scope_rows = []
        galaxy_tenants_list = []
        try:
            with connect_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT scope_id, total, active FROM pages_scope_stats_mv ORDER BY total DESC LIMIT 10")
                    for row in cur.fetchall():
                        scope_id = row[0] if row[0] else "__unscoped__"
                        label = format_scope_label(str(row[0])) if row[0] else "📂 Unscoped"
                        scope_rows.append((scope_id, int(row[1])))
                        galaxy_tenants_list.append({"name": label, "count": int(row[1]), "scope": scope_id})
        except Exception:
            logger.warning("dashboard: scope MV read failed", exc_info=True)
    if not mv_stats or not galaxy_tenants_list:
        # Fallback: direct queries (cold start, MV not yet populated)
        galaxy_type_data = {}
        try:
            with connect_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT memory_type, COUNT(*) FROM pages WHERE is_archived = FALSE GROUP BY memory_type ORDER BY COUNT(*) DESC"
                    )
                    galaxy_type_data = {r[0]: r[1] for r in cur.fetchall()}
        except Exception:
            galaxy_type_data = {"fact": 0, "conversation": 0}

    # Tenant-scoped type data for search
    galaxy_tenant_types = {}
    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                for t in galaxy_tenants_list:
                    sid = t["scope"]
                    cur.execute(
                        "SELECT memory_type, COUNT(*) FROM pages WHERE is_archived = FALSE AND scope_id = %s GROUP BY memory_type",
                        (sid,)
                    )
                    galaxy_tenant_types[sid] = {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        galaxy_tenant_types = {}

    # Tier stats
    try:
        from hermesclaw.consolidation import compute_tier_stats
        with connect_db() as conn:
            tier_stats = compute_tier_stats(conn)
    except Exception:
        tier_stats = {"hot": 0, "warm": 0, "standard": 0, "cold": 0}
    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM pages WHERE confidence >= 0.7")
                galaxy_high_conf = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM pages WHERE confidence >= 0.4 AND confidence < 0.7")
                galaxy_med_conf = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM pages WHERE confidence < 0.4")
                galaxy_low_conf = int(cur.fetchone()[0] or 0)
    except Exception:
        galaxy_high_conf = galaxy_med_conf = galaxy_low_conf = 0

    # All pages for galaxy node content
    galaxy_items = []
    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '3s'")
                cur.execute(
                    "SELECT p.id, p.content, p.scope_id, p.memory_type, p.importance, p.confidence, p.sentiment, p.created_at, "
                    "COALESCE((SELECT string_agg(t.tag, ',') FROM tags t WHERE t.page_id = p.id), '') AS tags "
                    "FROM pages p ORDER BY p.id DESC LIMIT 20000"
                )
                for row in cur.fetchall():
                    tags_list = (row[8] or "").split(",") if row[8] else []
                    galaxy_items.append({
                        "id": row[0],
                        "content": (row[1] or "")[:200],
                        "scope": (row[2] or "").strip() if (row[2] or "").strip() else "__unscoped__",
                        "type": row[3] or "fact",
                        "importance": float(row[4] or 0),
                        "confidence": float(row[5] or 0),
                        "sentiment": float(row[6] or 0),
                        "ts": str(row[7]) if row[7] else "",
                        "tags": tags_list[:5],
                    })
    except Exception as ex:
        logger.warning("galaxy items load failed: %s", ex, exc_info=True)

    return _jinja_env.get_template("dashboard.html").render(
        total_items=total_items,
        galaxy_tenants=galaxy_tenants_list,
        galaxy_high_conf=galaxy_high_conf,
        galaxy_med_conf=galaxy_med_conf,
        galaxy_low_conf=galaxy_low_conf,
        galaxy_total=galaxy_total,
        galaxy_items=galaxy_items,
        galaxy_type_data=galaxy_type_data,
        galaxy_tenant_types=galaxy_tenant_types,
        watchdog_pending_text=watchdog_pending_text,
        watchdog_updated_text=watchdog_updated_text,
        watchdog_pending_value=watchdog_pending_value,
        watchdog_last_synced_id=watchdog_last_synced_id,
        watchdog_latest_source_id=watchdog_latest_source_id,
        watchdog_last_error=watchdog_last_error,
        watchdog_progress_text=(
            f"last_synced_id={int(watchdog_last_synced_id)} | "
            f"latest_source_id={int(watchdog_latest_source_id)}"
            if watchdog_last_synced_id is not None and watchdog_latest_source_id is not None
            else "last_synced_id=n/a | latest_source_id=n/a"
        ),
        optimizer_msg=optimizer_msg,
        dry_run_msg=dry_run_msg,
        restore_msg=restore_msg,
        query=query or "",
        scope_options=scope_options,
        active_scope=active_scope,
        selected_scope_label=(
            "All users/scopes" if active_scope == DASHBOARD_SCOPE_ALL else
            "Unscoped (legacy rows)" if active_scope == DASHBOARD_SCOPE_UNSCOPED else
            format_scope_label(active_scope)
        ),
        selected_scope_total_count=selected_scope_total_count,
        safe_health_stale_days=safe_health_stale_days,
        safe_health_confidence=safe_health_confidence,
        safe_health_limit=safe_health_limit,
        before_id=before_id,
        next_cursor_id=next_cursor_id,
        prev_cursor_id=prev_cursor_id,
        has_next=has_next,
        rows=rows,
        review=review,
        dry_run=dry_run,
        version_info=version_info_val,
        update_status=update_status_val,
        tier_stats=tier_stats,
        memory_type=memory_type or "",
        days_back=days_back or "",
        AUTO_UPDATE_ENABLED=AUTO_UPDATE_ENABLED,
        AUTO_UPDATE_APPLY=AUTO_UPDATE_APPLY,
        AUTO_UPDATE_INTERVAL_SECONDS=AUTO_UPDATE_INTERVAL_SECONDS,
        latest_manual_batch_id=latest_manual_batch_id,
    )


# ---------------------------------------------------------------------------
# Optimizer & Update endpoints
# ---------------------------------------------------------------------------
@router.post("/optimizer/run", dependencies=[Depends(get_current_username)])
async def run_optimizer_now():
    stats = await run_in_threadpool(run_decay_and_archive_once)
    return {"status": "ok", "optimizer": stats}


@router.post("/optimizer/tiers", dependencies=[Depends(get_current_username)])
async def run_optimizer_tiers_now():
    """Recalculate memory tiers and run consolidation."""
    stats = await run_in_threadpool(run_tier_assignment)
    return {"status": "ok", "tiers": stats}


@router.post("/optimizer/dedup", dependencies=[Depends(get_current_username)])
async def run_optimizer_dedup(dry_run: bool = False):
    """Auto-merge duplicate/similar memories."""
    result = await run_in_threadpool(lambda: find_and_merge_duplicates(dry_run=dry_run))
    return {"status": "ok", "dedup": result}


@router.post("/optimizer/reflect", dependencies=[Depends(get_current_username)])
async def run_optimizer_reflect():
    """Analyze all memories for contradictions and generate scope summaries."""
    import ollama
    from hermesclaw.config import OLLAMA_HOST
    _client = ollama.Client(host=OLLAMA_HOST)
    with connect_db() as conn:
        result = analyze_memories(conn, llm_generate=_client.generate)
    return {"status": "ok", "reflection": result}


@router.get("/episodic/timeline", dependencies=[Depends(get_current_username)])
async def episodic_timeline(scope_id: str | None = None, project: str | None = None,
                            limit: int = 50, days_back: int | None = None):
    """Get episodic memory timeline."""
    with connect_db() as conn:
        timeline = get_timeline(conn, scope_id=scope_id, project=project, limit=limit, days_back=days_back)
    return {"status": "ok", "timeline": timeline}


@router.post("/episodic/record", dependencies=[Depends(get_current_username)])
async def episodic_record(title: str = Form(...), description: str = Form(""),
                          episode_type: str = Form("event"), scope_id: str | None = Form(None),
                          project: str | None = Form(None)):
    """Manually record an episodic memory."""
    with connect_db() as conn:
        eid = record_episode(conn, title=title, description=description, episode_type=episode_type,
                             scope_id=scope_id, project=project)
    return {"status": "ok", "episode_id": eid}


@router.get("/graph/rag", dependencies=[Depends(get_current_username)])
async def graph_rag(q: str = ""):
    """GraphRAG: search memories via entity graph traversal."""
    if not q:
        return {"status": "ok", "results": []}
    entities = [e.strip() for e in q.replace(",", " ").split() if len(e.strip()) > 2]
    with connect_db() as conn:
        results = graph_rag_search(conn, entities, query_text=q)
    return {"status": "ok", "results": results, "query_entities": entities}


@router.get("/ask", dependencies=[Depends(get_current_username)])
async def ask(q: str = "", scope_id: str | None = None):
    """Natural language question → vector + graph retrieval → LLM answer."""
    result = await run_in_threadpool(lambda: ask_question(q, scope_id=scope_id))
    return result


@router.get("/optimizer/dry_run", dependencies=[Depends(get_current_username)])
async def optimizer_dry_run(
    limit: int = 25,
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    selected_scope: str = DASHBOARD_SCOPE_ALL,
):
    safe_limit = max(1, min(limit, 200))
    safe_stale_days = max(1, min(stale_days, 3650))
    safe_confidence = clamp(confidence_threshold, 0.0, 1.0)
    dry_run_result = await run_in_threadpool(
        lambda: get_optimizer_dry_run(
            limit=safe_limit, stale_days=safe_stale_days,
            confidence_threshold=safe_confidence, selected_scope=selected_scope,
        )
    )
    return {"status": "ok", "dry_run": dry_run_result}


@router.get("/optimizer/review", dependencies=[Depends(get_current_username)])
async def review_optimizer_candidates(
    limit: int = 25,
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    selected_scope: str = DASHBOARD_SCOPE_ALL,
):
    safe_limit = max(1, min(limit, 200))
    safe_stale_days = max(1, min(stale_days, 3650))
    safe_confidence = clamp(confidence_threshold, 0.0, 1.0)
    review_result = await run_in_threadpool(
        lambda: get_optimizer_review(
            limit=safe_limit, stale_days=safe_stale_days,
            confidence_threshold=safe_confidence, selected_scope=selected_scope,
        )
    )
    return {"status": "ok", "review": review_result}


@router.post("/optimizer/archive_selected", dependencies=[Depends(get_current_username)])
async def optimizer_archive_selected(payload: ArchiveSelectionRequest):
    result = await run_in_threadpool(
        lambda: archive_selected_pages(payload.page_ids, archive_reason=payload.archive_reason)
    )
    return {"status": "ok", "archive": result}


@router.post("/optimizer/undo_latest_manual_archive", dependencies=[Depends(get_current_username)])
async def optimizer_undo_latest_manual_archive():
    latest_batch_id = await run_in_threadpool(get_latest_manual_archive_batch_id)
    if not latest_batch_id:
        return {"status": "ok", "undo": {"restored": 0, "deleted_from_archive": 0, "archive_batch_id": None}}
    result = await run_in_threadpool(restore_archive_batch, latest_batch_id)
    return {"status": "ok", "undo": result}


@router.get("/update/status", dependencies=[Depends(get_current_username)])
async def update_status():
    return {"status": "ok", "update": await run_in_threadpool(get_update_status, True)}


@router.post("/update/run", dependencies=[Depends(get_current_username)])
async def update_run():
    result = await run_in_threadpool(run_update)
    return {"status": "ok", "update": result}


# ---------------------------------------------------------------------------
# Dashboard form-action endpoints (redirect back)
# ---------------------------------------------------------------------------
@router.post("/optimizer/run_from_dashboard", dependencies=[Depends(get_current_username)])
async def run_optimizer_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    stats = await run_in_threadpool(run_decay_and_archive_once)
    message = (
        f"Optimizer completed: decayed={stats['decayed']}, "
        f"archived={stats['archived']}, deleted={stats['deleted']}"
    )
    return _dashboard_redirect(
        message, "optimizer_msg",
        query, selected_scope, page,
        health_stale_days, health_confidence_threshold, health_limit,
    )


@router.post("/optimizer/dry_run_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_dry_run_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    result = await run_in_threadpool(
        lambda: get_optimizer_dry_run(
            limit=max(1, min(health_limit, 50)),
            stale_days=max(1, min(health_stale_days, 3650)),
            confidence_threshold=clamp(health_confidence_threshold, 0.0, 1.0),
            selected_scope=selected_scope,
        )
    )
    message = (
        f"Dry run preview: would decay={result['would_decay_count']}, "
        f"would archive={result['would_archive_count']}"
    )
    return _dashboard_redirect(
        message, "dry_run_msg",
        query, selected_scope, page,
        health_stale_days, health_confidence_threshold, health_limit,
    )


@router.post("/optimizer/archive_selected_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_archive_selected_from_dashboard(
    selected_page_ids: list[int] = Form([]),
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    result = await run_in_threadpool(
        lambda: archive_selected_pages(selected_page_ids, archive_reason="manual_selected")
    )
    if result["requested"] == 0:
        msg = "No memories selected for archive."
        msg_key = "dry_run_msg"
    else:
        msg = (
            f"Archived selected memories: requested={result['requested']}, "
            f"archived={result['archived']}, deleted={result['deleted']}"
        )
        msg_key = "optimizer_msg"
    return _dashboard_redirect(
        msg, msg_key,
        query, selected_scope, page,
        health_stale_days, health_confidence_threshold, health_limit,
    )


@router.post("/optimizer/undo_latest_manual_archive_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_undo_latest_manual_archive_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    latest_batch_id = await run_in_threadpool(get_latest_manual_archive_batch_id)
    if not latest_batch_id:
        msg = "No manual archive batch available to undo."
    else:
        result = await run_in_threadpool(restore_archive_batch, latest_batch_id)
        msg = (
            f"Undo complete: restored={result['restored']}, "
            f"removed_archive_rows={result['deleted_from_archive']}, "
            f"batch={result['archive_batch_id']}"
        )
    return _dashboard_redirect(
        msg, "restore_msg",
        query, selected_scope, page,
        health_stale_days, health_confidence_threshold, health_limit,
    )


@router.get("/import", dependencies=[Depends(get_current_username)])
async def run_import(dry_run: bool = False):
    """Import Hermes sessions into Sidecar memory. Pass ?dry_run=true to preview."""
    result = await run_in_threadpool(lambda: import_hermes_sessions(dry_run=dry_run))
    return {"status": "ok", "import": result}


@router.post("/import", dependencies=[Depends(get_current_username)])
async def run_import_post():
    """Trigger a full import of Hermes sessions."""
    result = await run_in_threadpool(import_hermes_sessions)
    return {"status": "ok", "import": result}


# ── Memory Feedback ──

@router.post("/feedback/{page_id}", dependencies=[Depends(get_current_username)])
async def memory_feedback(page_id: int, helpful: bool = True):
    """Adjust memory importance based on user feedback (upvote/downvote)."""
    with connect_db() as conn:
        with conn.cursor() as cur:
            if helpful:
                cur.execute(
                    "UPDATE pages SET importance = LEAST(1.0, importance + 0.05), confidence = LEAST(1.0, confidence + 0.03), frequency = frequency + 1 WHERE id = %s",
                    (page_id,),
                )
            else:
                cur.execute(
                    "UPDATE pages SET importance = GREATEST(0.05, importance - 0.08), confidence = GREATEST(0.05, confidence - 0.05) WHERE id = %s",
                    (page_id,),
                )
            conn.commit()
            updated = cur.rowcount > 0
    return {"status": "ok", "updated": updated, "page_id": page_id, "helpful": helpful}


# ── Memory Editor ──

@router.post("/memory/update/{page_id}", dependencies=[Depends(get_current_username)])
async def memory_update(page_id: int, content: str = Form(""), memory_type: str | None = Form(None)):
    """Update a memory's content and/or type inline (Dashboard editor)."""
    with connect_db() as conn:
        with conn.cursor() as cur:
            if content:
                cur.execute("UPDATE pages SET content = %s, updated_at = NOW() WHERE id = %s", (content, page_id))
            if memory_type:
                cur.execute("UPDATE pages SET memory_type = %s WHERE id = %s", (memory_type, page_id))
            conn.commit()
            updated = cur.rowcount > 0
    return {"status": "ok", "updated": updated, "page_id": page_id}


@router.post("/memory/merge", dependencies=[Depends(get_current_username)])
async def memory_merge(source_ids: list[int] = Form(...), target_id: int | None = Form(None)):
    """Merge multiple memories into one. Source memories become children of the target."""
    if not source_ids:
        return {"status": "error", "detail": "source_ids required"}
    with connect_db() as conn:
        with conn.cursor() as cur:
            if target_id is None or target_id not in source_ids:
                cur.execute(
                    "SELECT id FROM pages WHERE id = ANY(%s) ORDER BY importance DESC LIMIT 1",
                    (source_ids,),
                )
                row = cur.fetchone()
                if not row:
                    return {"status": "error", "detail": "No valid source IDs"}
                target_id = row[0]
            others = [sid for sid in source_ids if sid != target_id]
            if others:
                cur.execute("UPDATE pages SET parent_id = %s, memory_tier = 'cold' WHERE id = ANY(%s)", (target_id, others))
                cur.execute("SELECT content FROM pages WHERE id = %s", (target_id,))
                target_content = cur.fetchone()[0]
                cur.execute("SELECT content FROM pages WHERE id = ANY(%s)", (others,))
                source_contents = [r[0] for r in cur.fetchall()]
                merged = target_content + "\n\n---\n\n" + "\n\n".join(source_contents)
                cur.execute("UPDATE pages SET content = %s, frequency = frequency + %s, importance = LEAST(1.0, importance + 0.05), updated_at = NOW() WHERE id = %s",
                    (merged, len(others), target_id))
            conn.commit()
    return {"status": "ok", "target_id": target_id, "merged": len(source_ids)}


# ── Memory Nudge (Hermes Built-In inspired periodic context) ──

@router.get("/nudge", dependencies=[Depends(get_current_username)])
async def memory_nudge(scope_id: str | None = None, limit: int = 5):
    """Return a 'memory nudge' — top important recent facts the agent should know about this scope/user."""
    from hermesclaw.scoring import build_scope_filter
    scope_clause, scope_params = build_scope_filter(scope_id, "p.scope_id")
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                           ROUND(EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_used, p.created_at))) / 86400.0, 1) AS age_days
                    FROM pages p
                    WHERE p.is_archived = FALSE
                      {scope_clause}
                    ORDER BY p.importance DESC, p.frequency DESC, p.last_used DESC
                    LIMIT %s""",
                (*scope_params, limit),
            )
            top = [
                {"id": r[0], "content": r[1], "type": r[2],
                 "importance": r[3], "confidence": r[4], "frequency": r[5], "age_days": r[6]}
                for r in cur.fetchall()
            ]

            cur.execute(
                f"""SELECT p.scope_id, COUNT(*) as cnt
                    FROM pages p
                    WHERE p.is_archived = FALSE {scope_clause}
                    GROUP BY p.scope_id ORDER BY cnt DESC LIMIT 5""",
                scope_params,
            )
            scope_breakdown = {r[0]: r[1] for r in cur.fetchall()}

    summary_prompt = f"Recent context for {scope_id or 'all scopes'}:\n" + "\n".join(
        f"- [{m['type']}] {m['content'][:200]}" for m in top
    )

    return {
        "status": "ok",
        "nudge": {
            "summary": summary_prompt,
            "top_memories": top,
            "scope_breakdown": scope_breakdown,
            "total_memories_in_scope": sum(scope_breakdown.values()) if scope_breakdown else None,
        },
    }


# ── Knowledge Graph endpoints ──

@router.get("/graph/entities", dependencies=[Depends(get_current_username)])
async def graph_top_entities(limit: int = 20):
    """Get top entities by frequency."""
    with connect_db() as conn:
        entities = get_top_entities(conn, limit=limit)
    return {"status": "ok", "entities": entities}


@router.get("/graph/entity/{entity_name}", dependencies=[Depends(get_current_username)])
async def graph_entity_detail(entity_name: str, depth: int = 2):
    """Get knowledge graph subgraph for an entity."""
    with connect_db() as conn:
        graph = query_entity_graph(conn, entity_name, depth=depth)
        memories = get_memories_for_entity(conn, entity_name)
    return {"status": "ok", "graph": graph, "memories": memories}


# ── Bi-Temporal: /why + /timeline ──

@router.get("/why/{page_id}", dependencies=[Depends(get_current_username)])
async def why_page(page_id: int):
    """Bi-temporal explanation: what this memory superseded, what superseded it, and version history."""
    with connect_db() as conn:
        with conn.cursor() as cur:
            # Current memory
            cur.execute(
                "SELECT id, content, memory_type, importance, confidence, created_at, "
                "valid_to, superseded_by, stability, frequency "
                "FROM pages WHERE id = %s", (page_id,)
            )
            current = cur.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Memory not found")

            current_memory = {
                "id": current[0], "content": current[1], "type": current[2],
                "importance": current[3], "confidence": current[4],
                "created_at": current[5].isoformat() if current[5] else None,
                "valid_to": current[6].isoformat() if current[6] else None,
                "superseded_by": current[7],
                "stability": current[8], "frequency": current[9],
            }

            # What it superseded (memories where this id is in their superseded_by)
            cur.execute(
                "SELECT id, content, memory_type, created_at, valid_to, importance, confidence "
                "FROM pages WHERE superseded_by = %s ORDER BY created_at DESC LIMIT 5",
                (page_id,)
            )
            superseded = [
                {"id": r[0], "content": r[1], "type": r[2],
                 "created_at": r[3].isoformat() if r[3] else None,
                 "valid_to": r[4].isoformat() if r[4] else None,
                 "importance": r[5], "confidence": r[6]}
                for r in cur.fetchall()
            ]

            # What superseded this memory (the id in this memory's superseded_by)
            sup_id = current[7]
            superseded_by_info = None
            if sup_id:
                cur.execute(
                    "SELECT id, content, memory_type, created_at, importance, confidence "
                    "FROM pages WHERE id = %s", (sup_id,)
                )
                r = cur.fetchone()
                if r:
                    superseded_by_info = {
                        "id": r[0], "content": r[1], "type": r[2],
                        "created_at": r[3].isoformat() if r[3] else None,
                        "importance": r[4], "confidence": r[5],
                    }

            # Version history from memory_versions table
            cur.execute(
                "SELECT id, content, memory_type, importance, confidence, version, "
                "change_reason, created_at "
                "FROM memory_versions WHERE page_id = %s ORDER BY version ASC",
                (page_id,)
            )
            versions = [
                {"id": r[0], "content": r[1], "type": r[2],
                 "importance": r[3], "confidence": r[4],
                 "version": r[5], "reason": r[6],
                 "created_at": r[7].isoformat() if r[7] else None}
                for r in cur.fetchall()
            ]

    return {
        "status": "ok",
        "page_id": page_id,
        "current": current_memory,
        "superseded": superseded,
        "superseded_by": superseded_by_info,
        "versions": versions,
        "total_versions": len(versions),
    }


@router.get("/timeline", dependencies=[Depends(get_current_username)])
async def bi_timeline(
    scope_id: str | None = None,
    limit: int = 50,
    days_back: int | None = None,
):
    """Bi-temporal timeline: show all memory changes (supersessions, version history) over time."""
    from hermesclaw.scoring import build_scope_filter
    scope_clause, scope_params = build_scope_filter(scope_id, "p.scope_id")
    time_clause = ""
    time_params: list = []
    if days_back:
        time_clause = " AND p.created_at >= NOW() - INTERVAL '%s days'"
        time_params = [days_back]

    with connect_db() as conn:
        with conn.cursor() as cur:
            # 1) Supersessions: memories that were invalidated (valid_to IS NOT NULL)
            cur.execute(
                f"""
                SELECT p.id, p.content, p.memory_type, p.created_at, p.valid_to,
                       p.superseded_by, p.importance, p.confidence,
                       sup.content AS superseded_by_content,
                       sup.created_at AS superseded_at
                FROM pages p
                LEFT JOIN pages sup ON p.superseded_by = sup.id
                WHERE p.valid_to IS NOT NULL
                  {scope_clause} {time_clause}
                ORDER BY p.valid_to DESC
                LIMIT %s
                """,
                scope_params + time_params + [limit],
            )
            supersessions = [
                {"id": r[0], "content": r[1], "type": r[2],
                 "created_at": r[3].isoformat() if r[3] else None,
                 "valid_to": r[4].isoformat() if r[4] else None,
                 "superseded_by_id": r[5],
                 "superseded_by_content": r[8],
                 "superseded_at": r[9].isoformat() if r[9] else None,
                 "importance": r[6], "confidence": r[7],
                 "event_type": "superseded"}
                for r in cur.fetchall()
            ]

            # 2) Versions: major edits from memory_versions
            cur.execute(
                f"""
                SELECT mv.page_id, mv.content, mv.memory_type, mv.created_at,
                       mv.version, mv.change_reason, mv.importance, mv.confidence,
                       p.content AS current_content
                FROM memory_versions mv
                JOIN pages p ON mv.page_id = p.id
                WHERE mv.version > 1
                  {scope_clause} {time_clause}
                ORDER BY mv.created_at DESC
                LIMIT %s
                """,
                scope_params + time_params + [limit],
            )
            edits = [
                {"page_id": r[0], "content": r[1], "type": r[2],
                 "created_at": r[3].isoformat() if r[3] else None,
                 "version": r[4], "reason": r[5],
                 "importance": r[6], "confidence": r[7],
                 "current_content": r[8],
                 "event_type": "edit"}
                for r in cur.fetchall()
            ]

            # 3) New memories (first capture, no version > 1, created recently)
            cur.execute(
                f"""
                SELECT p.id, p.content, p.memory_type, p.created_at,
                       p.importance, p.confidence, p.frequency
                FROM pages p
                WHERE p.id NOT IN (SELECT DISTINCT page_id FROM memory_versions WHERE version > 1)
                  AND p.created_at >= NOW() - INTERVAL '7 days'
                  {scope_clause} {time_clause}
                ORDER BY p.created_at DESC
                LIMIT %s
                """,
                scope_params + time_params + [limit],
            )
            new_memories = [
                {"id": r[0], "content": r[1], "type": r[2],
                 "created_at": r[3].isoformat() if r[3] else None,
                 "importance": r[4], "confidence": r[5], "frequency": r[6],
                 "event_type": "new"}
                for r in cur.fetchall()
            ]

    # Merge and sort all events by created_at desc
    all_events: list[dict] = []
    for e in supersessions:
        all_events.append({"timestamp": e.get("valid_to") or e.get("created_at"), **e})
    for e in edits:
        all_events.append({"timestamp": e.get("created_at"), **e})
    for e in new_memories:
        all_events.append({"timestamp": e.get("created_at"), **e})

    all_events.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    return {
        "status": "ok",
        "timeline": all_events[:limit],
        "total": len(all_events),
        "supersessions": len(supersessions),
        "edits": len(edits),
        "new_memories": len(new_memories),
    }


@router.get("/graph/search", dependencies=[Depends(get_current_username)])
async def graph_search(q: str = ""):
    """Search entities by name prefix."""
    if not q:
        return {"status": "ok", "entities": []}
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, entity_type, frequency FROM entities WHERE name ILIKE %s ORDER BY frequency DESC LIMIT 20",
                (f"%{q}%",),
            )
            entities = [{"id": r[0], "name": r[1], "type": r[2], "frequency": r[3]} for r in cur.fetchall()]
    return {"status": "ok", "entities": entities}


@router.get("/galaxy/item/{page_id}")
async def galaxy_item_lazy(page_id: int):
    """Lazy-load a single galaxy item's content on demand."""
    from hermesclaw.db import connect_db
    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT p.id, p.content, p.scope_id, p.memory_type, p.importance, p.confidence, p.sentiment, p.created_at, "
                    "COALESCE((SELECT string_agg(t.tag, ',') FROM tags t WHERE t.page_id = p.id), '') AS tags "
                    "FROM pages p WHERE p.id = %s AND p.is_archived = FALSE", (page_id,)
                )
                row = cur.fetchone()
                if not row:
                    return {"status": "not_found"}
                tags_list = (row[8] or "").split(",") if row[8] else []
                return {
                    "status": "ok",
                    "item": {
                        "id": row[0],
                        "content": (row[1] or "")[:200],
                        "scope": (row[2] or "").strip() if (row[2] or "").strip() else "__unscoped__",
                        "type": row[3] or "fact",
                        "importance": float(row[4] or 0),
                        "confidence": float(row[5] or 0),
                        "sentiment": float(row[6] or 0),
                        "ts": str(row[7]) if row[7] else "",
                        "tags": tags_list[:5],
                    },
                }
    except Exception as ex:
        return {"status": "error", "message": str(ex)}


@router.post("/update/run_from_dashboard", dependencies=[Depends(get_current_username)])
async def update_run_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    result = await run_in_threadpool(run_update)
    if result.get("updated"):
        msg = "✅ Update applied — system restarts now. Wait ~30s and reload."
    elif result.get("message"):
        stderr = result.get("stderr", "")
        msg = f"❌ {result['message']}"
        if stderr:
            msg += f" — {stderr[:200]}"
    else:
        # Show git hash even when nothing to update
        ver = get_version_info()
        sha = ver.get("git", {}).get("short_sha", "?") if ver.get("git") else "?"
        msg = f"No update applied. Current: {sha}"
    return _dashboard_redirect(
        msg, "optimizer_msg",
        query, selected_scope, page,
        health_stale_days, health_confidence_threshold, health_limit,
    )


# ── Policy Engine Audit Endpoint ──────────────────────────────────────────

POLICY_AUDIT_PATH = os.path.join(
    os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")),
    "policy_audit.jsonl",
)


@router.get("/policy/audit")
async def policy_audit(limit: int = 50):
    """Return the last N entries from the Policy Engine audit log."""
    if not os.path.exists(POLICY_AUDIT_PATH):
        return {"entries": [], "active": False}
    try:
        with open(POLICY_AUDIT_PATH, encoding="utf-8") as f:
            all_lines = [l.strip() for l in f if l.strip()]
        entries = []
        for l in all_lines[-limit:]:
            try:
                entries.append(json.loads(l))
            except json.JSONDecodeError:
                continue
        return {"entries": entries, "active": True}
    except Exception as e:
        return {"entries": [], "active": False, "error": str(e)}
