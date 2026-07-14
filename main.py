"""HermesClawZero API — FastAPI application factory."""

import time
import threading
import logging
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from hermesclaw import logger
from hermesclaw.config import (
    API_KEY, AUTO_UPDATE_ENABLED, AUTO_UPDATE_APPLY, AUTO_UPDATE_INTERVAL_SECONDS,
    DECAY_INTERVAL_SECONDS,
)
from hermesclaw.auth import (
    validate_security_startup, _is_rate_limited, _client_ip,
    WATCHDOG_STATUS,
)
from hermesclaw.db import ensure_phase1_schema, cleanup_orphaned_embeddings, close_db_pool, connect_db
from hermesclaw.optimizer import run_decay_and_archive_once, run_tier_assignment
from hermesclaw.dedup import find_and_merge_duplicates
from hermesclaw.reflection import analyze_memories
from hermesclaw.episodic import ensure_episodic_schema
from hermesclaw.update import get_update_status, run_update
from hermesclaw.importer import import_hermes_sessions
from hermesclaw.routes import router

app = FastAPI()

# Mount static files (favicons, logos)
from fastapi.staticfiles import StaticFiles
import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# Serve favicon at root (browser default path) — bypasses auth
from fastapi.responses import RedirectResponse, FileResponse


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_root():
    ico_path = os.path.join(_static_dir, "favicon.ico")
    if os.path.exists(ico_path):
        return FileResponse(ico_path, media_type="image/x-icon")
    return RedirectResponse(url="/static/favicon.ico")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg_root():
    return FileResponse(os.path.join(_static_dir, "favicon.svg"), media_type="image/svg+xml")


@app.get("/site.webmanifest", include_in_schema=False)
async def manifest_root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "site.webmanifest"), media_type="application/manifest+json")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def url_api_key_middleware(request: Request, call_next):
    path = request.url.path
    exempt_exact_paths = {"/", "/openapi.json", "/docs", "/healthz", "/version"}
    exempt_prefixes = (
        "/docs/", "/dashboard", "/delete", "/export",
        "/tag_auto/", "/page_html", "/optimizer/", "/update/",
        "/import", "/graph", "/feedback", "/galaxy/",
        "/static/", "/favicon", "/site.webmanifest",
    )

    if path in exempt_exact_paths or any(path.startswith(p) for p in exempt_prefixes):
        return await call_next(request)

    key = request.headers.get("x-api-key") or request.query_params.get("key")
    from hermesclaw.config import API_KEY
    if key != API_KEY:
        return HTMLResponse("Unauthorized", status_code=401)
    return await call_next(request)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    limited, retry_after = _is_rate_limited(path, _client_ip(request), time.time())
    if limited:
        return JSONResponse(
            {"detail": "Rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Background workers with graceful shutdown
# ---------------------------------------------------------------------------
_shutdown_event = threading.Event()


def decay_loop() -> None:
    while not _shutdown_event.is_set():
        try:
            stats = run_decay_and_archive_once()
            logger.info(
                "[OPTIMIZER] decay=%s archived=%s deleted=%s",
                stats["decayed"], stats["archived"], stats["deleted"],
            )
            # Refresh 2M-scale materialized views
            try:
                from hermesclaw.db import refresh_materialized_views
                mv = refresh_materialized_views()
                logger.info("[MV] views refreshed: total=%s active=%s", mv["total"], mv["active"])
            except Exception as mv_ex:
                logger.debug("[MV] refresh error (non-fatal): %s", mv_ex)
            # Run tier assignment every cycle too
            try:
                tiers = run_tier_assignment()
                logger.info("[TIER] %s", tiers.get("tiers"))
            except Exception as tier_ex:
                logger.warning("[TIER] error: %s", tier_ex)
            # Run auto-dedup every cycle
            try:
                dedup = find_and_merge_duplicates()
                if dedup["hard_dedup"] > 0 or dedup["soft_merge"] > 0:
                    logger.info("[DEDUP] hard=%s soft=%s", dedup["hard_dedup"], dedup["soft_merge"])
            except Exception as dex:
                logger.debug("[DEDUP] error (non-fatal): %s", dex)
        except Exception as ex:
            logger.exception("[OPTIMIZER] error: %s", ex)
        _shutdown_event.wait(DECAY_INTERVAL_SECONDS)


def auto_update_loop() -> None:
    while not _shutdown_event.is_set():
        try:
            if AUTO_UPDATE_ENABLED and AUTO_UPDATE_APPLY:
                status = get_update_status(fetch_remote=True)
                if status.get("available"):
                    result = run_update()
                    logger.info("[UPDATE] auto-apply result: %s", result.get("updated"))
        except Exception as ex:
            logger.exception("[UPDATE] loop error: %s", ex)
        _shutdown_event.wait(AUTO_UPDATE_INTERVAL_SECONDS)


def _supervised_sync_loop() -> None:
    """Run memory_sync.run_sync with crash detection and automatic restart."""
    import memory_sync
    from hermesclaw.auth import SYNC_LIVENESS

    while not _shutdown_event.is_set():
        SYNC_LIVENESS["running"] = True
        try:
            memory_sync.run_sync()
        except Exception as ex:
            logger.exception("[SYNC] worker crashed, will restart: %s", ex)
            SYNC_LIVENESS["last_error_ts"] = int(time.time())
            SYNC_LIVENESS["last_error"] = str(ex)
        finally:
            SYNC_LIVENESS["running"] = False
        SYNC_LIVENESS["restart_count"] = int(SYNC_LIVENESS.get("restart_count") or 0) + 1
        logger.warning("[SYNC] restarting sync worker in 5s (restart #%s)", SYNC_LIVENESS["restart_count"])
        _shutdown_event.wait(5)


def _auto_import_loop() -> None:
    """Periodically import new Hermes sessions (auto-sync)."""
    while not _shutdown_event.is_set():
        try:
            result = import_hermes_sessions()
            if result.get("sessions_imported", 0) > 0:
                logger.info("[AUTO-SYNC] Imported %d new session(s)", result["sessions_imported"])
        except Exception as ex:
            logger.debug("[AUTO-SYNC] check failed (non-fatal): %s", ex)
        _shutdown_event.wait(300)  # every 5 minutes


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup_event():
    validate_security_startup()

    try:
        ensure_phase1_schema()
        # Ensure episodic schema
        with connect_db() as conn:
            ensure_episodic_schema(conn)
        orphaned_deleted = cleanup_orphaned_embeddings()
        logger.info("[SCHEMA] cleanup_orphaned_embeddings deleted=%s", orphaned_deleted)
        run_decay_and_archive_once()

        # First-run import: auto-import Hermes sessions into Sidecar memory
        try:
            result = import_hermes_sessions()
            if result.get("status") == "ok":
                logger.info(
                    "[IMPORT] Hermes sessions: %d found, %d imported, %d messages — %d errors",
                    result.get("sessions_found", 0),
                    result.get("sessions_imported", 0),
                    result.get("messages_imported", 0),
                    len(result.get("errors", [])),
                )
            else:
                logger.info("[IMPORT] Skipped: %s", result.get("reason", "unknown"))
        except Exception as ex:
            logger.warning("[IMPORT] Non-fatal error: %s", ex)

        # Auto-recover: restore from pre-rebuild backup first, else Hermes state.db,
        # else restore from pages_archive (previous optimizer decay).
        # Handles the case where docker rebuild wipes the DB volume.
        # Runs in background thread to avoid blocking server startup.
        try:
            with connect_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM pages")
                    page_count = cur.fetchone()[0]
            if page_count < 100:
                logger.warning("[RECOVERY] Only %d pages found — scheduling restore pipeline", page_count)

                def _run_recovery():
                    import subprocess, sys, os, json

                    # Step 1: Try pre-rebuild backup restore first
                    backup_script = "/app/repo/migrations/pre_rebuild_backup.py"
                    if os.path.exists(backup_script):
                        try:
                            logger.info("[RECOVERY] Checking for pre-rebuild backup...")
                            result = subprocess.run(
                                [sys.executable, backup_script, "restore",
                                 "--db-host", os.environ.get("DB_HOST", "localhost"),
                                 "--db-port", os.environ.get("DB_PORT", "5432"),
                                 "--db-pass", os.environ.get("DB_PASSWORD", ""),
                                 "--db-name", os.environ.get("DB_NAME", "gbrain"),
                                 "--db-user", os.environ.get("DB_USER", "postgres")],
                                capture_output=True, text=True, timeout=300,
                            )
                            for line in result.stdout.splitlines():
                                logger.info("[RECOVERY] %s", line)
                            if result.returncode == 0:
                                restored = json.loads(result.stdout.strip().split("\n")[-1])
                                if restored.get("restored", 0) > 0:
                                    logger.info("[RECOVERY] Pre-rebuild backup restored %d pages!", restored["restored"])
                                    return  # Skip fallbacks — backup was better
                            else:
                                logger.warning("[RECOVERY] Backup restore stderr: %s", result.stderr[:500])
                        except Exception as ex:
                            logger.warning("[RECOVERY] Backup restore failed (non-fatal): %s", ex)
                    else:
                        logger.info("[RECOVERY] pre_rebuild_backup.py not found — skipping backup restore")

                    # Step 2: Restore from pages_archive (previous optimizer decay)
                    try:
                        with connect_db() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO pages (id, content, memory_type, importance, confidence,
                                                       frequency, source, scope_id, created_at, updated_at)
                                    SELECT DISTINCT ON (pa.page_id)
                                        pa.page_id, pa.content,
                                        COALESCE(pa.memory_type, 'conversation'),
                                        COALESCE(pa.importance, 0.5),
                                        COALESCE(pa.confidence, 0.7),
                                        COALESCE(pa.frequency, 1),
                                        pa.source, pa.scope_id,
                                        pa.created_at, pa.updated_at
                                    FROM pages_archive pa
                                    WHERE NOT EXISTS (SELECT 1 FROM pages p WHERE p.id = pa.page_id)
                                    ORDER BY pa.page_id, pa.archived_at DESC
                                """)
                                restored = cur.rowcount
                                conn.commit()
                                logger.info("[RECOVERY] Restored %d pages from archive!", restored)
                    except Exception as ex:
                        logger.warning("[RECOVERY] Archive restore failed (non-fatal): %s", ex)

                    # Step 3: Fallback — import Hermes state.db
                    try:
                        result = subprocess.run(
                            [sys.executable, "/app/repo/migrations/import_from_hermes_db.py",
                             "--hermes-db", "/hermes_state/state.db", "--minimal"],
                            capture_output=True, text=True, timeout=600,
                        )
                        for line in result.stdout.splitlines():
                            logger.info("[RECOVERY] %s", line)
                        if result.returncode != 0:
                            logger.warning("[RECOVERY] Import stderr: %s", result.stderr[:500])
                        else:
                            logger.info("[RECOVERY] Hermes state.db import completed successfully")
                    except Exception as ex:
                        logger.warning("[RECOVERY] State.db import failed (non-fatal): %s", ex)

                threading.Thread(target=_run_recovery, daemon=True).start()
        except Exception as ex:
            logger.warning("[RECOVERY] Non-fatal error: %s", ex)

        # First-run tier assignment + graph schema
        try:
            tiers = run_tier_assignment()
            logger.info("[TIER] memory tiers: %s", tiers.get("tiers"))
        except Exception as ex:
            logger.warning("[TIER] Non-fatal error: %s", ex)
    except Exception as ex:
        raise RuntimeError(f"Startup failed during database initialization: {ex}") from ex

    # Start background workers
    from hermesclaw.embedding_queue import ensure_worker
    ensure_worker()  # starts async embedding batch processor
    threading.Thread(target=decay_loop, daemon=True).start()
    if AUTO_UPDATE_ENABLED:
        threading.Thread(target=auto_update_loop, daemon=True).start()
    threading.Thread(target=_supervised_sync_loop, daemon=True).start()
    threading.Thread(target=_auto_import_loop, daemon=True).start()


@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutdown signal received, stopping background workers...")
    _shutdown_event.set()
    close_db_pool()


# ---------------------------------------------------------------------------
# Register routes
# ---------------------------------------------------------------------------
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
