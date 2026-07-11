"""Authentication: API key auth, dashboard sessions, rate limiting."""

import time
import secrets
import hashlib
import hmac
import base64
import threading
import logging
from fastapi import HTTPException, status, Request, Response, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from cachetools import TTLCache
from collections import deque
from hermesclaw.config import (
    API_KEY, DASHBOARD_PASSWORD, DASHBOARD_SESSION_COOKIE,
    DASHBOARD_SESSION_TTL_SECONDS, DASHBOARD_SESSION_SECRET,
    RATE_LIMIT_RULES,
)

logger = logging.getLogger("hermesclaw.auth")

security = HTTPBasic(auto_error=False)

# TTLCache auto-evicts stale IP buckets so unbounded client IPs cannot leak memory.
RATE_LIMIT_STATE: TTLCache = TTLCache(
    maxsize=int(__import__("os").getenv("RATE_LIMIT_MAX_IPS", "10000")), ttl=60
)
RATE_LIMIT_LOCK = threading.Lock()

WATCHDOG_STATUS: dict = {
    "pending": None,
    "last_synced_id": None,
    "latest_source_id": None,
    "last_error": None,
    "updated_at": None,
}

# Sync worker liveness, updated by the supervised sync thread.
SYNC_LIVENESS: dict = {
    "running": False,
    "last_success_ts": None,
    "last_error_ts": None,
    "last_error": None,
    "restart_count": 0,
}


# ---------------------------------------------------------------------------
# Security startup validation
# ---------------------------------------------------------------------------
def validate_security_startup() -> None:
    if not API_KEY:
        raise RuntimeError("API_KEY is required.")

    if DASHBOARD_PASSWORD == "admin":
        raise RuntimeError(
            "DASHBOARD_PASSWORD must be explicitly set and cannot remain the default 'admin'."
        )

    if not DASHBOARD_SESSION_SECRET:
        raise RuntimeError("DASHBOARD_SESSION_SECRET is required and must be explicitly set.")

    weak_session_secrets = {"change-this-dashboard-secret", "admin", "password", "123456"}
    if DASHBOARD_SESSION_SECRET.lower() in weak_session_secrets:
        raise RuntimeError("DASHBOARD_SESSION_SECRET is too weak. Set a strong random secret.")

    if DASHBOARD_SESSION_SECRET == API_KEY:
        raise RuntimeError("DASHBOARD_SESSION_SECRET must not reuse API_KEY.")


# ---------------------------------------------------------------------------
# Dashboard session tokens (HMAC-signed)
# ---------------------------------------------------------------------------
def _build_dashboard_session_token(username: str) -> str:
    issued_at = str(int(time.time()))
    payload = f"{username}:{issued_at}"
    signature = hmac.new(
        DASHBOARD_SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    token = base64.urlsafe_b64encode(
        f"{payload}:{signature}".encode("utf-8")
    ).decode("utf-8")
    return token


def _validate_dashboard_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        username, issued_at_str, signature = decoded.split(":", 2)
        payload = f"{username}:{issued_at_str}"
        expected_signature = hmac.new(
            DASHBOARD_SESSION_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        issued_at = int(issued_at_str)
        if int(time.time()) - issued_at > DASHBOARD_SESSION_TTL_SECONDS:
            return None
        if username != "admin":
            return None
        return username
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dependency callables
# ---------------------------------------------------------------------------
def get_current_username(
    request: Request,
    response: Response,
    credentials: HTTPBasicCredentials | None = Depends(security),
):
    session_user = _validate_dashboard_session_token(
        request.cookies.get(DASHBOARD_SESSION_COOKIE)
    )
    if session_user:
        return session_user

    if credentials is not None:
        correct_password = secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
        correct_username = secrets.compare_digest(credentials.username, "admin")
        if correct_username and correct_password:
            session_token = _build_dashboard_session_token(credentials.username)
            response.set_cookie(
                key=DASHBOARD_SESSION_COOKIE,
                value=session_token,
                httponly=True,
                samesite="lax",
                max_age=DASHBOARD_SESSION_TTL_SECONDS,
                path="/",
            )
            return credentials.username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_api_key(request: Request):
    key = request.headers.get("x-api-key") or request.query_params.get("key")
    if not key or not API_KEY or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_rate_limited(path: str, ip: str, now_ts: float) -> tuple[bool, int]:
    rule = RATE_LIMIT_RULES.get(path)
    if not rule:
        return False, 0

    max_requests, window_seconds = rule
    key = (path, ip)
    with RATE_LIMIT_LOCK:
        bucket = RATE_LIMIT_STATE.get(key)
        if bucket is None:
            bucket = deque()
            RATE_LIMIT_STATE[key] = bucket
        cutoff = now_ts - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= max_requests:
            retry_after = max(1, int(window_seconds - (now_ts - bucket[0])))
            return True, retry_after

        bucket.append(now_ts)
    return False, 0
