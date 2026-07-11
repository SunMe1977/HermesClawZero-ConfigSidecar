"""All route handlers."""

import math
import time
import html
import logging
import threading
from urllib.parse import quote_plus
import jinja2

from fastapi import APIRouter, HTTPException, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
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
from hermesclaw.memory import _capture_sync, _capture_batch_sync, _search_sync
from hermesclaw.scoring import clamp, normalize_scope_id, build_scope_filter, format_scope_label
from hermesclaw.embeddings import provider_runtime_info
from hermesclaw.optimizer import (
    run_decay_and_archive_once, get_optimizer_review, get_optimizer_dry_run,
    archive_selected_pages, get_latest_manual_archive_batch_id, restore_archive_batch,
)
from hermesclaw.update import get_update_status, run_update, get_version_info

logger = logging.getLogger("hermesclaw.routes")

router = APIRouter()

_CHANGE_SECRET = threading.Event()
_shutdown_event = threading.Event()

# Jinja2 environment for the dashboard template
_template_dir = __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "..", "templates")
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_template_dir),
    autoescape=True,
)


def _dashboard_redirect(
    msg: str,
    msg_key: str = "optimizer_msg",
    query: str = "",
    selected_scope: str = DASHBOARD_SCOPE_ALL,
    page: int = 1,
    health_stale_days: int = 14,
    health_confidence_threshold: float = 0.3,
    health_limit: int = 8,
) -> RedirectResponse:
    """Build a redirect to /dashboard with all current parameters preserved."""
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))
    scope = quote_plus((selected_scope or DASHBOARD_SCOPE_ALL).strip() or DASHBOARD_SCOPE_ALL)
    return RedirectResponse(
        url=(
            f"/dashboard?{msg_key}={quote_plus(msg)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&selected_scope={scope}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


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
    return await run_in_threadpool(_capture_sync, text, scope_id, chat_id, body)


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
):
    return await run_in_threadpool(_search_sync, query, limit, rerank_results, scope_id, chat_id)


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
async def export_data():
    def _export_sync():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, content FROM pages")
                rows = cur.fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]

    return await run_in_threadpool(_export_sync)


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
    page: int = 1,
    optimizer_msg: str | None = None,
    dry_run_msg: str | None = None,
    restore_msg: str | None = None,
    health_stale_days: int = 14,
    health_confidence_threshold: float = 0.3,
    health_limit: int = 8,
):
    per_page = 20
    offset = (page - 1) * per_page
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
                        f"SELECT id, content FROM pages WHERE content ILIKE %s{list_scope_clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                        [f"%{query}%"] + list_scope_params + [per_page, offset],
                    )
                    rows = cur.fetchall()
                    cur.execute(
                        f"SELECT COUNT(*) FROM pages WHERE content ILIKE %s{list_scope_clause}",
                        [f"%{query}%"] + list_scope_params,
                    )
                    total_items = cur.fetchone()[0]
                else:
                    cur.execute(
                        f"SELECT id, content FROM pages WHERE 1=1{list_scope_clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                        list_scope_params + [per_page, offset],
                    )
                    rows = cur.fetchall()
                    cur.execute(
                        f"SELECT COUNT(*) FROM pages WHERE 1=1{list_scope_clause}",
                        list_scope_params,
                    )
                    total_items = cur.fetchone()[0]
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

    total_pages = math.ceil(total_items / per_page)

    scope_options = [
        (DASHBOARD_SCOPE_ALL, "All users/scopes"),
        (DASHBOARD_SCOPE_UNSCOPED, "Unscoped (legacy rows)"),
    ]
    for sid, count in scope_rows:
        scope_options.append((str(sid), format_scope_label(str(sid), int(count))))

    if active_scope not in {o[0] for o in scope_options}:
        scope_options.append((active_scope, f"{format_scope_label(active_scope)} (selected)"))

    return _jinja_env.get_template("dashboard.html").render(
        total_items=total_items,
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
        page=page,
        total_pages=total_pages,
        rows=rows,
        review=review,
        dry_run=dry_run,
        version_info=version_info_val,
        update_status=update_status_val,
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
    msg = "Update run complete." if result.get("updated") else result.get("message", "No update applied.")
    return _dashboard_redirect(
        msg, "optimizer_msg",
        query, selected_scope, page,
        health_stale_days, health_confidence_threshold, health_limit,
    )
