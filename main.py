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
        "/import", "/graph", "/feedback", "/search", "/memory", "/nudge", "/episodic", "/ask",
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

        # First-run tier assignment + graph schema
        try:
            tiers = run_tier_assignment()
            logger.info("[TIER] memory tiers: %s", tiers.get("tiers"))
        except Exception as ex:
            logger.warning("[TIER] Non-fatal error: %s", ex)
    except Exception as ex:
        raise RuntimeError(f"Startup failed during database initialization: {ex}") from ex

    # Start background workers
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
    uvicorn.run(app, host="0.0.0.0", port=8010)
