from fastapi import Body, FastAPI, HTTPException, Depends, status, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.concurrency import run_in_threadpool
from psycopg_pool import ConnectionPool
import ollama
import requests
import os
import threading
import html
import secrets
import math
import time
import json
import base64
import hashlib
import hmac
import logging
from urllib.parse import quote_plus
import uuid
import subprocess
from pathlib import Path
from cachetools import TTLCache
from collections import deque
from pydantic import BaseModel

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("hermesclaw")

app = FastAPI()

# Security
security = HTTPBasic(auto_error=False)
API_KEY = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
DASHBOARD_SESSION_COOKIE = "dashboard_session"
DASHBOARD_SESSION_TTL_SECONDS = int(os.getenv("DASHBOARD_SESSION_TTL_SECONDS", "43200"))
DASHBOARD_SESSION_SECRET = (os.getenv("DASHBOARD_SESSION_SECRET", "") or "").strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


REPO_DIR = os.getenv("UPDATE_REPO_DIR", os.getcwd())
UPDATE_REMOTE = os.getenv("AUTO_UPDATE_REMOTE", "origin")
UPDATE_BRANCH = os.getenv("AUTO_UPDATE_BRANCH", "main")
AUTO_UPDATE_ENABLED = env_bool("AUTO_UPDATE_ENABLED", False)
AUTO_UPDATE_APPLY = env_bool("AUTO_UPDATE_APPLY", False)
AUTO_UPDATE_INTERVAL_SECONDS = max(60, int(os.getenv("AUTO_UPDATE_INTERVAL_MINUTES", "60")) * 60)
UPDATE_RESTART_COMMAND = os.getenv("UPDATE_RESTART_COMMAND", "")

class CaptureRequest(BaseModel):
    text: str
    scope_id: str | None = None
    chat_id: str | None = None


class BatchCaptureItem(BaseModel):
    msg_id: int | None = None
    text: str
    scope_id: str | None = None
    chat_id: str | None = None


class BatchCaptureRequest(BaseModel):
    items: list[BatchCaptureItem]
    skip_dedupe: bool = True


class ArchiveSelectionRequest(BaseModel):
    page_ids: list[int]
    archive_reason: str = "manual_review"


class WatchdogStatusRequest(BaseModel):
    pending: int
    last_synced_id: int
    latest_source_id: int
    last_error: str | None = None


WATCHDOG_STATUS = {
    "pending": None,
    "last_synced_id": None,
    "latest_source_id": None,
    "last_error": None,
    "updated_at": None,
}

# Sync worker liveness, updated by the supervised sync thread.
SYNC_LIVENESS = {
    "running": False,
    "last_success_ts": None,
    "last_error_ts": None,
    "last_error": None,
    "restart_count": 0,
}

DASHBOARD_SCOPE_ALL = "all"
DASHBOARD_SCOPE_UNSCOPED = "__unscoped__"
SCOPE_LABELS_JSON = (os.getenv("SCOPE_LABELS_JSON", "") or "").strip()


def _load_scope_aliases(raw_json: str) -> dict[str, str]:
    if not raw_json:
        return {}
    try:
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict):
            return {}
        aliases: dict[str, str] = {}
        for key, value in parsed.items():
            key_str = str(key).strip()
            value_str = str(value).strip()
            if key_str and value_str:
                aliases[key_str[:200]] = value_str[:120]
        return aliases
    except Exception:
        return {}


SCOPE_ALIASES = _load_scope_aliases(SCOPE_LABELS_JSON)

RATE_LIMIT_RULES = {
    "/capture": (30, 60),
    "/search": (60, 60),
}
# TTLCache auto-evicts stale IP buckets so unbounded client IPs cannot leak memory.
RATE_LIMIT_STATE: TTLCache = TTLCache(maxsize=int(os.getenv("RATE_LIMIT_MAX_IPS", "10000")), ttl=60)
RATE_LIMIT_LOCK = threading.Lock()

OPENROUTER_MAX_RETRIES = 3
OPENROUTER_RETRY_BASE_SECONDS = 1.0
OPENROUTER_DEGRADED_MESSAGE = "OpenRouter temporarily unavailable. Memories are saved but search will be degraded."
ALLOW_EMBEDDING_SCHEMA_RESET = env_bool("ALLOW_EMBEDDING_SCHEMA_RESET", False)


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

# Auth Helpers
def _build_dashboard_session_token(username: str) -> str:
    issued_at = str(int(time.time()))
    payload = f"{username}:{issued_at}"
    signature = hmac.new(
        DASHBOARD_SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("utf-8")
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


def get_current_username(
    request: Request,
    response: Response,
    credentials: HTTPBasicCredentials | None = Depends(security),
):
    session_user = _validate_dashboard_session_token(request.cookies.get(DASHBOARD_SESSION_COOKIE))
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


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

@app.middleware("http")
async def url_api_key(request, call_next):
    path = request.url.path
    exempt_exact_paths = {
        "/",
        "/openapi.json",
        "/docs",
        "/healthz",
        "/version",
    }
    exempt_prefixes = (
        "/docs/",
        "/dashboard",
        "/delete",
        "/export",
        "/tag_auto/",
        "/page_html",
        "/optimizer/",
        "/update/",
    )

    if path in exempt_exact_paths or any(path.startswith(prefix) for prefix in exempt_prefixes):
        return await call_next(request)

    key = request.headers.get("x-api-key") or request.query_params.get("key")
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


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/dashboard", status_code=307)


@app.get("/healthz")
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

    # memory_sync LIVENESS is available once the sync module has been loaded.
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
        "sync": {
            "worker": sync_snapshot,
            "ingest": sync_ingest,
        },
    }
    if db_error:
        payload["database_error"] = db_error
    return JSONResponse(payload, status_code=200 if db_ok else 503)


# ---------------------------------------------------------
#  DATABASE + OLLAMA CONFIG
# ---------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "gbrain-postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "gbrain")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

_db_pool: ConnectionPool | None = None
_db_pool_lock = threading.Lock()


def get_db_pool() -> ConnectionPool:
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    with _db_pool_lock:
        if _db_pool is None:
            conninfo = (
                f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
                f"user={DB_USER} password={DB_PASSWORD}"
            )
            _db_pool = ConnectionPool(
                conninfo=conninfo,
                min_size=int(os.getenv("DB_POOL_MIN", "2")),
                max_size=int(os.getenv("DB_POOL_MAX", "10")),
                timeout=float(os.getenv("DB_POOL_TIMEOUT", "30")),
            )
            logger.info(
                "DB connection pool created (min=%s max=%s)",
                os.getenv("DB_POOL_MIN", "2"),
                os.getenv("DB_POOL_MAX", "10"),
            )
    return _db_pool


def close_db_pool() -> None:
    global _db_pool
    if _db_pool is not None:
        _db_pool.close()
        _db_pool = None


def connect_db():
    return get_db_pool().connection()


def embedding_to_pgvector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11435")
client = ollama.Client(host=OLLAMA_HOST)
AI_PROVIDER = (os.getenv("AI_PROVIDER", "ollama") or "ollama").strip().lower()
EMBEDDING_PROVIDER = (os.getenv("EMBEDDING_PROVIDER", "auto") or "auto").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
OPENROUTER_EMBED_MODEL = os.getenv("OPENROUTER_EMBED_MODEL", "text-embedding-3-small")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "models/text-embedding-004")
EMBEDDING_DIM = os.getenv("EMBEDDING_DIM", "").strip()
DECAY_INTERVAL_SECONDS = int(os.getenv("MEMORY_DECAY_INTERVAL_SECONDS", "21600"))


def resolve_embedding_provider() -> str:
    if EMBEDDING_PROVIDER not in {"auto", "ollama", "openai", "openrouter", "gemini"}:
        raise HTTPException(
            status_code=500,
            detail="Invalid EMBEDDING_PROVIDER. Use one of: auto, ollama, openai, openrouter, gemini",
        )

    if EMBEDDING_PROVIDER != "auto":
        return EMBEDDING_PROVIDER

    if AI_PROVIDER in {"ollama", "openai", "openrouter", "gemini"}:
        return AI_PROVIDER

    if AI_PROVIDER == "anthropic":
        # Anthropic does not offer native text embeddings; auto-route to available embed provider.
        if OPENROUTER_API_KEY:
            return "openrouter"
        if OPENAI_API_KEY:
            return "openai"
        if GEMINI_API_KEY:
            return "gemini"
        raise HTTPException(
            status_code=500,
            detail=(
                "AI_PROVIDER=anthropic requires EMBEDDING_PROVIDER or an embedding key "
                "(OPENROUTER_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY)."
            ),
        )

    raise HTTPException(
        status_code=500,
        detail="Unsupported AI_PROVIDER. Use one of: ollama, openai, gemini, anthropic, openrouter",
    )


def _raise_embedding_upstream_error(provider: str, response: requests.Response) -> None:
    status_code = int(response.status_code or 500)
    body_text = (response.text or "").lower()

    # Keep details concise and avoid echoing upstream payloads that may include sensitive hints.
    if status_code in {401, 403} or "invalid_api_key" in body_text or "incorrect api key" in body_text:
        raise HTTPException(
            status_code=401,
            detail=f"{provider} embedding authentication failed. Check provider API key and selected AI_PROVIDER/EMBEDDING_PROVIDER.",
        )

    if status_code == 429:
        raise HTTPException(status_code=429, detail=f"{provider} embedding rate limit exceeded.")

    if 400 <= status_code < 500:
        raise HTTPException(status_code=400, detail=f"{provider} embedding request rejected by upstream provider.")

    raise HTTPException(status_code=502, detail=f"{provider} embedding upstream error.")


def _openrouter_embeddings_request(payload: dict, timeout_seconds: int) -> dict:
    last_error: str | None = None
    for attempt in range(1, OPENROUTER_MAX_RETRIES + 1):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout_seconds,
            )

            if response.status_code < 400:
                return response.json()

            if response.status_code == 429:
                logger.warning("[OPENROUTER] rate-limited on attempt %s/%s", attempt, OPENROUTER_MAX_RETRIES)

            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = f"status={response.status_code}"
                if attempt < OPENROUTER_MAX_RETRIES:
                    time.sleep(OPENROUTER_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
                    continue
                raise HTTPException(status_code=503, detail=OPENROUTER_DEGRADED_MESSAGE)

            _raise_embedding_upstream_error("OpenRouter", response)
        except requests.RequestException as ex:
            last_error = str(ex)
            if attempt < OPENROUTER_MAX_RETRIES:
                time.sleep(OPENROUTER_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
                continue
            break

    logger.warning("[OPENROUTER] embedding retries exhausted: %s", last_error or 'unknown error')
    raise HTTPException(status_code=503, detail=OPENROUTER_DEGRADED_MESSAGE)


def generate_embedding(text: str) -> list[float]:
    provider = resolve_embedding_provider()
    if provider == "ollama":
        resp = client.embeddings(model="nomic-embed-text", prompt=text)
        return resp["embedding"]

    if provider == "openai":
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY is required when AI_PROVIDER=openai")
        response = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": OPENAI_EMBED_MODEL, "input": text},
            timeout=45,
        )
        if response.status_code >= 400:
            _raise_embedding_upstream_error("OpenAI", response)
        data = response.json()
        return data["data"][0]["embedding"]

    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is required when AI_PROVIDER=openrouter")
        data = _openrouter_embeddings_request(
            payload={"model": OPENROUTER_EMBED_MODEL, "input": text},
            timeout_seconds=45,
        )
        return data["data"][0]["embedding"]

    if provider == "gemini":
        if not GEMINI_API_KEY:
            raise HTTPException(status_code=500, detail="GEMINI_API_KEY is required when embedding provider is gemini")
        model_name = GEMINI_EMBED_MODEL if GEMINI_EMBED_MODEL.startswith("models/") else f"models/{GEMINI_EMBED_MODEL}"
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/{model_name}:embedContent?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "model": model_name,
                "content": {"parts": [{"text": text}]},
            },
            timeout=45,
        )
        if response.status_code >= 400:
            _raise_embedding_upstream_error("Gemini", response)
        data = response.json()
        return data["embedding"]["values"]

    raise HTTPException(
        status_code=500,
        detail=(
            "Unsupported embedding provider. "
            "Use one of: ollama, openai, openrouter, gemini"
        ),
    )


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    provider = resolve_embedding_provider()

    if provider == "openai":
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY is required when AI_PROVIDER=openai")
        response = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": OPENAI_EMBED_MODEL, "input": texts},
            timeout=90,
        )
        if response.status_code >= 400:
            _raise_embedding_upstream_error("OpenAI", response)
        data = response.json().get("data", [])
        data_sorted = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data_sorted]

    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is required when AI_PROVIDER=openrouter")
        response_payload = _openrouter_embeddings_request(
            payload={"model": OPENROUTER_EMBED_MODEL, "input": texts},
            timeout_seconds=90,
        )
        data = response_payload.get("data", [])
        data_sorted = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data_sorted]

    # Fallback providers currently run per-text.
    return [generate_embedding(t) for t in texts]


def provider_runtime_info() -> dict:
    resolved = None
    resolution_error = None
    try:
        resolved = resolve_embedding_provider()
    except Exception as ex:
        resolution_error = str(ex)

    return {
        "ai_provider": AI_PROVIDER,
        "embedding_provider": EMBEDDING_PROVIDER,
        "resolved_embedding_provider": resolved,
        "keys_present": {
            "openai": bool(OPENAI_API_KEY),
            "openrouter": bool(OPENROUTER_API_KEY),
            "gemini": bool(GEMINI_API_KEY),
            "anthropic": bool(ANTHROPIC_API_KEY),
        },
        "resolution_error": resolution_error,
    }


def infer_embedding_dimension() -> int:
    if EMBEDDING_DIM:
        try:
            parsed = int(EMBEDDING_DIM)
            if parsed > 0:
                return parsed
        except Exception:
            pass

    if EMBEDDING_PROVIDER != "auto":
        provider = EMBEDDING_PROVIDER
    else:
        provider = AI_PROVIDER
        if provider == "anthropic":
            if OPENROUTER_API_KEY:
                provider = "openrouter"
            elif OPENAI_API_KEY:
                provider = "openai"
            elif GEMINI_API_KEY:
                provider = "gemini"
            else:
                provider = "openrouter"

    dims_by_provider = {
        "ollama": 768,
        "openai": 1536,
        "openrouter": 1536,
        "gemini": 768,
    }
    return dims_by_provider.get(provider, 768)


def ensure_embeddings_schema(expected_dim: int) -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'embeddings'
                )
                """
            )
            exists = bool(cur.fetchone()[0])

            if not exists:
                cur.execute(
                    f"""
                    CREATE TABLE embeddings (
                      id SERIAL PRIMARY KEY,
                      page_id INT REFERENCES pages(id) ON DELETE CASCADE,
                      embedding vector({expected_dim}),
                      created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                conn.commit()
                return

            cur.execute(
                """
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = 'public'
                  AND c.relname = 'embeddings'
                  AND a.attname = 'embedding'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                LIMIT 1
                """
            )
            row = cur.fetchone()
            current_type = row[0] if row else ""
            expected_type = f"vector({expected_dim})"

            if current_type != expected_type:
                message = (
                    "[SCHEMA] embeddings.embedding type mismatch "
                    f"({current_type or 'unknown'} -> {expected_type}). "
                    "Automatic destructive reset is disabled. "
                    "Run rebuild_embeddings.py or set ALLOW_EMBEDDING_SCHEMA_RESET=true to force reset."
                )
                if not ALLOW_EMBEDDING_SCHEMA_RESET:
                    logger.error(message)
                    raise RuntimeError(message)

                logger.warning("%s Proceeding with forced reset due to ALLOW_EMBEDDING_SCHEMA_RESET=true.", message)
                cur.execute("DROP TABLE IF EXISTS embeddings")
                cur.execute(
                    f"""
                    CREATE TABLE embeddings (
                      id SERIAL PRIMARY KEY,
                      page_id INT REFERENCES pages(id) ON DELETE CASCADE,
                      embedding vector({expected_dim}),
                      created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
            conn.commit()


def _capture_sync(
    text: str | None = None,
    scope_id: str | None = None,
    chat_id: str | None = None,
    body: CaptureRequest | None = None,
):
    capture_text = text if text is not None else (body.text if body else None)
    capture_scope_id = normalize_scope_id(scope_id if scope_id is not None else (body.scope_id if body else None))
    capture_chat_id = derive_chat_id(chat_id if chat_id is not None else (body.chat_id if body else None), capture_scope_id)
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
                cur.execute("INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)", (page_id, emb_str))
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


def _capture_batch_sync(body: BatchCaptureRequest):
    if not body.items:
        raise HTTPException(status_code=400, detail="items are required")

    prepared: list[tuple[int | None, str | None, str, str, dict]] = []
    for item in body.items:
        text = (item.text or "").strip()
        if not text:
            continue
        meta = score_memory(text)
        normalized_scope_id = normalize_scope_id(item.scope_id)
        prepared.append((item.msg_id, normalized_scope_id, derive_chat_id(item.chat_id, normalized_scope_id), text, meta))

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

    inserted_msg_ids: list[int] = []
    with connect_db() as conn:
        with conn.cursor() as cur:
            page_ids: list[int] = []
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

    return {
        "status": "ok",
        "processed": len(dedupe_candidates),
        "msg_ids": inserted_msg_ids,
    }


def _search_sync(
    query: str = "",
    limit: int = 5,
    rerank_results: bool = False,
    scope_id: str | None = None,
    chat_id: str = "global",
):
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
                      AND (%s IS NULL OR scope_id = %s)
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

    candidates: dict[int, dict] = {}

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
                      AND (%s IS NULL OR p.scope_id = %s)
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
                    AND (%s IS NULL OR p.scope_id = %s)
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
        if existing:
            existing["lexical_rank"] = float(r[7]) if r[7] is not None else None
        else:
            candidates[r[0]] = {
                "id": r[0],
                "content": r[1],
                "memory_type": r[2],
                "importance": r[3],
                "confidence": r[4],
                "frequency": r[5],
                "age_days": float(r[6] or 0.0),
                "vector_distance": None,
                "lexical_rank": float(r[7]) if r[7] is not None else None,
            }

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


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def classify_memory_type(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in ["prefer", "favorite", "likes", "dislike", "usually use"]):
        return "preference"
    if any(k in lower for k in ["project", "milestone", "deadline", "deploy", "release"]):
        return "project"
    if any(k in lower for k in ["skill", "learned", "can do", "expert", "proficient"]):
        return "skill"
    if any(k in lower for k in [" is ", " are ", " was ", " has ", " have "]):
        return "fact"
    return "conversation"


def estimate_sentiment(text: str) -> float:
    lower = text.lower()
    positives = ["great", "good", "love", "excellent", "success", "happy"]
    negatives = ["bad", "hate", "problem", "issue", "fail", "error"]
    pos = sum(lower.count(w) for w in positives)
    neg = sum(lower.count(w) for w in negatives)
    score = (pos - neg) / max(1, pos + neg, 4)
    return clamp(score, -1.0, 1.0)


def score_memory(text: str) -> dict:
    memory_type = classify_memory_type(text)
    sentiment = estimate_sentiment(text)

    base_importance = {
        "fact": 0.78,
        "preference": 0.82,
        "project": 0.86,
        "skill": 0.76,
        "conversation": 0.55,
    }[memory_type]
    base_confidence = {
        "fact": 0.82,
        "preference": 0.74,
        "project": 0.78,
        "skill": 0.72,
        "conversation": 0.62,
    }[memory_type]
    ttl_days = {
        "fact": None,
        "preference": 365,
        "project": 180,
        "skill": 365,
        "conversation": 90,
    }[memory_type]

    length_bonus = min(0.2, len(text) / 7000.0)
    importance = clamp(base_importance + length_bonus + (0.03 if "!" in text else 0.0))
    confidence = clamp(base_confidence + (0.04 if len(text) > 40 else 0.0))

    return {
        "memory_type": memory_type,
        "importance": importance,
        "confidence": confidence,
        "sentiment": sentiment,
        "source": "capture",
        "ttl_days": ttl_days,
        "score": round(importance * confidence, 4),
    }


def normalize_scope_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:200]


def normalize_chat_id(value: str | None) -> str:
    if value is None:
        return "global"
    normalized = value.strip()
    if not normalized:
        return "global"
    return normalized[:200]


def derive_chat_id(explicit_chat_id: str | None, scope_id: str | None) -> str:
    normalized = normalize_chat_id(explicit_chat_id)
    if normalized != "global":
        return normalized

    scope_value = normalize_scope_id(scope_id)
    if scope_value and ":" in scope_value:
        return normalize_chat_id(scope_value.split(":", 1)[1])
    return "global"


def build_scope_filter(selected_scope: str | None, column_name: str = "scope_id") -> tuple[str, list]:
    raw = (selected_scope or "").strip()
    if not raw or raw == DASHBOARD_SCOPE_ALL:
        return "", []
    if raw == DASHBOARD_SCOPE_UNSCOPED:
        return f" AND {column_name} IS NULL", []

    normalized = normalize_scope_id(raw)
    if normalized is None:
        return "", []
    return f" AND {column_name} = %s", [normalized]


def format_scope_label(scope_id: str, count: int | None = None) -> str:
    clean_scope = normalize_scope_id(scope_id) or scope_id
    alias = SCOPE_ALIASES.get(clean_scope)
    suffix = f" ({count})" if count is not None else ""

    prefix = clean_scope
    local_id = None
    if ":" in clean_scope:
        prefix, local_id = clean_scope.split(":", 1)

    prefix_lower = prefix.strip().lower()
    if alias:
        return f"{alias} [{clean_scope}]{suffix}"

    if local_id:
        id_part = local_id.strip()
        if prefix_lower in {"telegram", "tg"}:
            return f"Telegram chat {id_part}{suffix}"
        if prefix_lower in {"openclaw", "hermes"}:
            return f"{prefix.capitalize()} user {id_part}{suffix}"
        return f"{prefix} {id_part}{suffix}"

    return f"{clean_scope}{suffix}"


def normalize_lexical_rank(rank: float | None) -> float:
    if rank is None:
        return 0.0
    return clamp(rank / (rank + 1.0))


def normalize_vector_distance(distance: float | None) -> float:
    if distance is None:
        return 0.0
    return clamp(1.0 / (1.0 + max(0.0, distance)))


def normalize_frequency(freq: int | None) -> float:
    freq_value = max(1, int(freq or 1))
    return clamp(math.log10(1 + min(freq_value, 1000)) / math.log10(101))


def normalize_recency(age_days: float | None) -> float:
    if age_days is None:
        return 0.5
    return clamp(math.exp(-max(0.0, age_days) / 30.0))


def compute_hybrid_score(item: dict) -> tuple[float, dict]:
    vector_component = normalize_vector_distance(item.get("vector_distance"))
    lexical_component = normalize_lexical_rank(item.get("lexical_rank"))
    importance_component = clamp(float(item.get("importance") or 0.5))
    confidence_component = clamp(float(item.get("confidence") or 0.5))
    recency_component = normalize_recency(item.get("age_days"))
    frequency_component = normalize_frequency(item.get("frequency"))

    base_score = (
        0.45 * vector_component
        + 0.25 * lexical_component
        + 0.15 * importance_component
        + 0.10 * recency_component
        + 0.05 * frequency_component
    )
    final_score = round(base_score * (0.5 + 0.5 * confidence_component), 6)

    explain = {
        "components": {
            "vector": round(vector_component, 4),
            "lexical": round(lexical_component, 4),
            "importance": round(importance_component, 4),
            "confidence": round(confidence_component, 4),
            "recency": round(recency_component, 4),
            "frequency": round(frequency_component, 4),
        },
        "weights": {
            "vector": 0.45,
            "lexical": 0.25,
            "importance": 0.15,
            "recency": 0.10,
            "frequency": 0.05,
        },
    }

    reasons = []
    if lexical_component >= 0.35:
        reasons.append("strong keyword overlap")
    if vector_component >= 0.6:
        reasons.append("high semantic similarity")
    if importance_component >= 0.75:
        reasons.append("high importance memory")
    if recency_component >= 0.6:
        reasons.append("recently used")
    if frequency_component >= 0.5:
        reasons.append("frequently retrieved")

    explain["reasons"] = reasons or ["balanced hybrid match"]
    explain["final_score"] = final_score
    return final_score, explain


def ensure_phase1_schema() -> None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS memory_type TEXT NOT NULL DEFAULT 'conversation'")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS importance REAL NOT NULL DEFAULT 0.5")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS confidence REAL NOT NULL DEFAULT 0.8")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS frequency INT NOT NULL DEFAULT 1")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS sentiment REAL NOT NULL DEFAULT 0.0")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'capture'")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS ttl_days INT")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS last_used TIMESTAMP DEFAULT NOW()")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS last_retrieved TIMESTAMP")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS scope_id TEXT")
            cur.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS chat_id TEXT NOT NULL DEFAULT 'global'")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pages_archive (
                  archive_id SERIAL PRIMARY KEY,
                  page_id INT,
                  content TEXT NOT NULL,
                  memory_type TEXT,
                  importance REAL,
                  confidence REAL,
                  frequency INT,
                  sentiment REAL,
                  source TEXT,
                  ttl_days INT,
                  created_at TIMESTAMP,
                  updated_at TIMESTAMP,
                  last_used TIMESTAMP,
                  last_retrieved TIMESTAMP,
                  archived_at TIMESTAMP DEFAULT NOW(),
                  archive_reason TEXT DEFAULT 'decay'
                )
                """
            )

            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_memory_type ON pages(memory_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_last_used ON pages(last_used DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_archived ON pages(is_archived)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_fts_content ON pages USING GIN (to_tsvector('english', content))")
            cur.execute("ALTER TABLE pages_archive ADD COLUMN IF NOT EXISTS archive_batch_id TEXT")
            cur.execute("ALTER TABLE pages_archive ADD COLUMN IF NOT EXISTS scope_id TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_archive_batch ON pages_archive(archive_batch_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_scope_id ON pages(scope_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_chat_id ON pages(chat_id)")
            conn.commit()

    ensure_embeddings_schema(infer_embedding_dimension())


def run_decay_and_archive_once() -> dict:
    with connect_db() as conn:
        with conn.cursor() as cur:
            # Natural confidence decay for stale, non-archived memories.
            cur.execute(
                """
                UPDATE pages
                SET confidence = GREATEST(0.05, confidence * 0.995),
                    updated_at = NOW()
                WHERE is_archived = FALSE
                  AND last_used < NOW() - INTERVAL '7 days'
                """
            )
            decayed_count = cur.rowcount

            # Archive stale memories by TTL or low confidence, then remove active rows.
            cur.execute(
                """
                WITH stale AS (
                    SELECT *
                    FROM pages
                    WHERE is_archived = FALSE
                      AND (
                        (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                        OR confidence < 0.18
                      )
                )
                INSERT INTO pages_archive (
                    page_id, content, memory_type, importance, confidence, frequency,
                    sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, archive_reason
                )
                SELECT id, content, memory_type, importance, confidence, frequency,
                       sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved,
                       CASE
                         WHEN ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW() THEN 'ttl_expired'
                         ELSE 'low_confidence'
                       END
                FROM stale
                """
            )
            archived_count = cur.rowcount

            cur.execute(
                """
                DELETE FROM pages
                WHERE is_archived = FALSE
                  AND (
                    (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                    OR confidence < 0.18
                  )
                """
            )
            deleted_count = cur.rowcount
            conn.commit()

    return {
        "decayed": decayed_count,
        "archived": archived_count,
        "deleted": deleted_count,
    }


def cleanup_orphaned_embeddings() -> int:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM embeddings
                WHERE page_id NOT IN (SELECT id FROM pages)
                """
            )
            deleted_count = int(cur.rowcount or 0)
            conn.commit()
    return deleted_count


def get_optimizer_review(
    limit: int = 25,
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    selected_scope: str | None = None,
) -> dict:
    scope_clause, scope_params = build_scope_filter(selected_scope, "p.scope_id")
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    p.id,
                    p.content,
                    p.memory_type,
                    p.importance,
                    p.confidence,
                    p.frequency,
                    p.ttl_days,
                    ROUND(EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_used, p.created_at))) / 86400.0, 2) AS age_days,
                    CASE
                        WHEN p.ttl_days IS NOT NULL AND p.created_at + (p.ttl_days || ' days')::interval < NOW() THEN 'ttl_expired'
                        WHEN p.confidence < %s THEN 'low_confidence'
                        WHEN COALESCE(p.last_used, p.created_at) < NOW() - (%s || ' days')::interval THEN 'stale'
                        ELSE 'healthy'
                    END AS review_reason
                FROM pages p
                WHERE p.is_archived = FALSE
                                    {scope_clause}
                  AND (
                        p.confidence < %s
                        OR COALESCE(p.last_used, p.created_at) < NOW() - (%s || ' days')::interval
                        OR (p.ttl_days IS NOT NULL AND p.created_at + (p.ttl_days || ' days')::interval < NOW())
                  )
                ORDER BY p.confidence ASC, age_days DESC
                LIMIT %s
                """,
                                tuple(scope_params) + (confidence_threshold, stale_days, confidence_threshold, stale_days, limit),
            )
            pending_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    page_id,
                    content,
                    memory_type,
                    importance,
                    confidence,
                    frequency,
                    ROUND(EXTRACT(EPOCH FROM (NOW() - archived_at)) / 86400.0, 2) AS days_since_archived,
                    archive_reason,
                    archived_at
                FROM pages_archive
                ORDER BY archived_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            archived_rows = cur.fetchall()

    pending_review = [
        {
            "id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "ttl_days": r[6],
            "age_days": float(r[7] or 0.0),
            "review_reason": r[8],
        }
        for r in pending_rows
    ]

    archived_recent = [
        {
            "page_id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "days_since_archived": float(r[6] or 0.0),
            "archive_reason": r[7],
            "archived_at": str(r[8]),
        }
        for r in archived_rows
    ]

    return {
        "thresholds": {
            "stale_days": stale_days,
            "confidence_threshold": confidence_threshold,
            "limit": limit,
        },
        "pending_review": pending_review,
        "archived_recent": archived_recent,
        "summary": {
            "pending_count": len(pending_review),
            "recent_archived_count": len(archived_recent),
        },
    }


def get_optimizer_dry_run(
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    limit: int = 25,
    selected_scope: str | None = None,
) -> dict:
    scope_clause, scope_params = build_scope_filter(selected_scope, "scope_id")
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM pages
                WHERE is_archived = FALSE
                    {scope_clause}
                    AND last_used < NOW() - INTERVAL '7 days'
                """,
                tuple(scope_params),
            )
            would_decay_count = int(cur.fetchone()[0])

            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM pages
                WHERE is_archived = FALSE
                    {scope_clause}
                    AND (
                    (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                    OR confidence < 0.18
                    )
                """,
                tuple(scope_params),
            )
            would_archive_count = int(cur.fetchone()[0])

            cur.execute(
                f"""
                SELECT
                    id,
                    content,
                    memory_type,
                    importance,
                    confidence,
                    frequency,
                    ROUND(EXTRACT(EPOCH FROM (NOW() - COALESCE(last_used, created_at))) / 86400.0, 2) AS age_days,
                    CASE
                        WHEN ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW() THEN 'ttl_expired'
                        WHEN confidence < 0.18 THEN 'low_confidence'
                        WHEN last_used < NOW() - INTERVAL '7 days' THEN 'stale_decay_candidate'
                        ELSE 'review_candidate'
                    END AS dry_run_reason
                FROM pages
                WHERE is_archived = FALSE
                    {scope_clause}
                    AND (
                    (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                    OR confidence < 0.18
                    OR last_used < NOW() - INTERVAL '7 days'
                    OR confidence < %s
                    OR COALESCE(last_used, created_at) < NOW() - (%s || ' days')::interval
                    )
                ORDER BY
                    CASE
                        WHEN ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW() THEN 0
                        WHEN confidence < 0.18 THEN 1
                        WHEN last_used < NOW() - INTERVAL '7 days' THEN 2
                        ELSE 3
                    END,
                    confidence ASC,
                    age_days DESC
                LIMIT %s
                """,
                tuple(scope_params) + (confidence_threshold, stale_days, limit),
            )
            sample_rows = cur.fetchall()

    sample = [
        {
            "id": r[0],
            "content": r[1],
            "memory_type": r[2],
            "importance": r[3],
            "confidence": r[4],
            "frequency": r[5],
            "age_days": float(r[6] or 0.0),
            "dry_run_reason": r[7],
        }
        for r in sample_rows
    ]

    return {
        "thresholds": {
            "stale_days": stale_days,
            "confidence_threshold": confidence_threshold,
            "limit": limit,
        },
        "would_decay_count": would_decay_count,
        "would_archive_count": would_archive_count,
        "sample": sample,
    }


def archive_selected_pages(page_ids: list[int], archive_reason: str = "manual_review", archive_batch_id: str | None = None) -> dict:
    selected_ids = sorted({int(pid) for pid in page_ids if int(pid) > 0})
    if not selected_ids:
        return {"requested": 0, "archived": 0, "deleted": 0, "ids": [], "archive_batch_id": archive_batch_id}

    batch_id = archive_batch_id or str(uuid.uuid4())

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH selected AS (
                    SELECT *
                    FROM pages
                    WHERE id = ANY(%s)
                      AND is_archived = FALSE
                )
                INSERT INTO pages_archive (
                    archive_batch_id, page_id, content, memory_type, importance, confidence, frequency,
                    sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, archive_reason
                )
                SELECT %s, id, content, memory_type, importance, confidence, frequency,
                       sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, %s
                FROM selected
                """,
                (selected_ids, batch_id, archive_reason),
            )
            archived_count = cur.rowcount

            cur.execute(
                "DELETE FROM pages WHERE id = ANY(%s) AND is_archived = FALSE",
                (selected_ids,),
            )
            deleted_count = cur.rowcount
            conn.commit()

    return {
        "requested": len(selected_ids),
        "archived": archived_count,
        "deleted": deleted_count,
        "ids": selected_ids,
        "archive_reason": archive_reason,
        "archive_batch_id": batch_id,
    }


def get_latest_manual_archive_batch_id() -> str | None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT archive_batch_id
                FROM pages_archive
                WHERE archive_reason = 'manual_selected'
                  AND archive_batch_id IS NOT NULL
                ORDER BY archived_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    return row[0] if row else None


def restore_archive_batch(archive_batch_id: str) -> dict:
    if not archive_batch_id:
        return {"restored": 0, "deleted_from_archive": 0, "archive_batch_id": archive_batch_id}

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pages (
                    content, memory_type, importance, confidence, frequency,
                    sentiment, source, ttl_days, created_at, updated_at, last_used, last_retrieved, is_archived
                )
                SELECT
                    content,
                    COALESCE(memory_type, 'conversation'),
                    COALESCE(importance, 0.5),
                    COALESCE(confidence, 0.8),
                    COALESCE(frequency, 1),
                    COALESCE(sentiment, 0.0),
                    COALESCE(source, 'restore'),
                    ttl_days,
                    COALESCE(created_at, NOW()),
                    NOW(),
                    COALESCE(last_used, NOW()),
                    last_retrieved,
                    FALSE
                FROM pages_archive
                WHERE archive_batch_id = %s
                """,
                (archive_batch_id,),
            )
            restored_count = cur.rowcount

            cur.execute(
                "DELETE FROM pages_archive WHERE archive_batch_id = %s",
                (archive_batch_id,),
            )
            deleted_archive_count = cur.rowcount
            conn.commit()

    return {
        "restored": restored_count,
        "deleted_from_archive": deleted_archive_count,
        "archive_batch_id": archive_batch_id,
    }


def _run_command(cmd: list[str], cwd: str = REPO_DIR, timeout: int = 45) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        ok = proc.returncode == 0
        return ok, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as ex:
        return False, "", str(ex)


def _git_available() -> bool:
    ok, out, _ = _run_command(["git", "rev-parse", "--is-inside-work-tree"])
    return ok and out.lower() == "true"


def get_update_status(fetch_remote: bool = True) -> dict:
    if not _git_available():
        return {
            "available": False,
            "error": "git repository not available in runtime environment",
        }

    if fetch_remote:
        _run_command(["git", "fetch", UPDATE_REMOTE, UPDATE_BRANCH], timeout=90)

    ok_head, head_sha, head_err = _run_command(["git", "rev-parse", "HEAD"])
    ok_remote, remote_sha, remote_err = _run_command(["git", "rev-parse", f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"])

    if not (ok_head and ok_remote):
        return {
            "available": False,
            "error": head_err or remote_err or "unable to determine git status",
        }

    available = head_sha != remote_sha
    return {
        "available": available,
        "local_sha": head_sha,
        "remote_sha": remote_sha,
        "branch": UPDATE_BRANCH,
        "remote": UPDATE_REMOTE,
        "auto_update_enabled": AUTO_UPDATE_ENABLED,
        "auto_update_apply": AUTO_UPDATE_APPLY,
        "auto_update_interval_seconds": AUTO_UPDATE_INTERVAL_SECONDS,
    }


def run_update() -> dict:
    status_before = get_update_status(fetch_remote=True)
    if status_before.get("error"):
        return {"updated": False, "status": status_before}

    if not status_before.get("available"):
        return {"updated": False, "status": status_before, "message": "already up to date"}

    ok_pull, out_pull, err_pull = _run_command(
        ["git", "pull", "--ff-only", UPDATE_REMOTE, UPDATE_BRANCH],
        timeout=120,
    )
    if not ok_pull:
        return {
            "updated": False,
            "status": status_before,
            "message": "git pull failed",
            "stdout": out_pull,
            "stderr": err_pull,
        }

    restart_result = {"ran": False, "ok": True, "stdout": "", "stderr": ""}
    if UPDATE_RESTART_COMMAND.strip():
        restart_result["ran"] = True
        try:
            proc = subprocess.run(
                UPDATE_RESTART_COMMAND,
                cwd=REPO_DIR,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
                shell=True,
            )
            restart_result["ok"] = proc.returncode == 0
            restart_result["stdout"] = (proc.stdout or "").strip()
            restart_result["stderr"] = (proc.stderr or "").strip()
        except Exception as ex:
            restart_result["ok"] = False
            restart_result["stderr"] = str(ex)

    status_after = get_update_status(fetch_remote=False)
    return {
        "updated": True,
        "status_before": status_before,
        "status_after": status_after,
        "restart": restart_result,
    }


def get_version_info() -> dict:
    base_version = os.getenv("APP_VERSION", "")
    if not base_version:
        version_file = Path(REPO_DIR) / "VERSION"
        if version_file.exists():
            base_version = version_file.read_text(encoding="utf-8").strip()
    if not base_version:
        base_version = "0.1.0"

    if not _git_available():
        return {"version": base_version, "base_version": base_version, "git": None}

    ok_count, commit_count, _ = _run_command(["git", "rev-list", "--count", "HEAD"])
    ok_sha, short_sha, _ = _run_command(["git", "rev-parse", "--short", "HEAD"])
    if ok_count and ok_sha:
        version = f"{base_version}+build.{commit_count}.{short_sha}"
        return {
            "version": version,
            "base_version": base_version,
            "git": {
                "commit_count": int(commit_count),
                "short_sha": short_sha,
            },
        }

    return {"version": base_version, "base_version": base_version, "git": None}


def auto_update_loop() -> None:
    while True:
        try:
            if AUTO_UPDATE_ENABLED and AUTO_UPDATE_APPLY:
                status = get_update_status(fetch_remote=True)
                if status.get("available"):
                    result = run_update()
                    logger.info("[UPDATE] auto-apply result: %s", result.get('updated'))
        except Exception as ex:
            logger.exception("[UPDATE] loop error: %s", ex)
        time.sleep(AUTO_UPDATE_INTERVAL_SECONDS)


def decay_loop() -> None:
    while True:
        try:
            stats = run_decay_and_archive_once()
            logger.info(
                "[OPTIMIZER] decay=%s archived=%s deleted=%s",
                stats['decayed'], stats['archived'], stats['deleted'],
            )
        except Exception as ex:
            logger.exception("[OPTIMIZER] error: %s", ex)
        time.sleep(DECAY_INTERVAL_SECONDS)


# ---------------------------------------------------------
#  CAPTURE & SEARCH
# ---------------------------------------------------------
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
                  AND (%s IS NULL OR p.scope_id = %s)
                ORDER BY dist ASC
                LIMIT 1
                """,
                                (emb_str, normalize_chat_id(chat_id), scope_id, scope_id),
            )
            row = cur.fetchone()
    if row and row[2] <= threshold: return {"id": row[0], "content": row[1], "distance": row[2]}
    return None

@app.post("/capture")
async def capture(
    text: str | None = None,
    scope_id: str | None = None,
    chat_id: str | None = None,
    body: CaptureRequest | None = Body(default=None),
):
    return await run_in_threadpool(_capture_sync, text, scope_id, chat_id, body)


@app.post("/capture/batch", dependencies=[Depends(require_api_key)])
async def capture_batch(body: BatchCaptureRequest):
    return await run_in_threadpool(_capture_batch_sync, body)

@app.post("/watchdog/status", dependencies=[Depends(require_api_key)])
async def watchdog_status_update(body: WatchdogStatusRequest):
    WATCHDOG_STATUS["pending"] = max(0, int(body.pending))
    WATCHDOG_STATUS["last_synced_id"] = int(body.last_synced_id)
    WATCHDOG_STATUS["latest_source_id"] = int(body.latest_source_id)
    WATCHDOG_STATUS["last_error"] = (body.last_error or "").strip() or None
    WATCHDOG_STATUS["updated_at"] = int(time.time())
    return {"status": "ok"}

@app.get("/search")
async def search(
    query: str = "",
    limit: int = 5,
    rerank_results: bool = False,
    scope_id: str | None = None,
    chat_id: str = "global",
):
    return await run_in_threadpool(_search_sync, query, limit, rerank_results, scope_id, chat_id)

def rerank(query: str, items: list[dict]) -> list[dict]:
    if not items: return items
    prompt = f"Query: {query}\n\nRe-rank these items by relevance (0-10 score), return JSON: [{{'id': id, 'score': score}}]\n"
    for item in items:
        prompt += f"ID: {item['id']}, Content: {item['content'][:200]}\n"
    
    resp = client.generate(model="llama3.1:8b", prompt=prompt)
    try:
        ranked = json.loads(resp['response'])
        ranked.sort(key=lambda x: x['score'], reverse=True)
        id_map = {r['id']: r for r in items}
        return [id_map[r['id']] for r in ranked if r['id'] in id_map]
    except (json.JSONDecodeError, KeyError, TypeError):
        return items

# ---------------------------------------------------------
#  ADMIN & DASHBOARD (PROTECTED)
# ---------------------------------------------------------
@app.post("/delete", dependencies=[Depends(get_current_username)])
async def delete_page(page_id: int = Form(...)):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pages WHERE id = %s", (page_id,))
            conn.commit()
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/export", dependencies=[Depends(get_current_username)])
async def export_data():
    def _export_data_sync() -> list[dict]:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, content FROM pages")
                rows = cur.fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]

    return await run_in_threadpool(_export_data_sync)

@app.post("/tag_auto/{page_id}", dependencies=[Depends(get_current_username)])
async def tag_auto(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    
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

@app.get("/page_html", response_class=HTMLResponse, dependencies=[Depends(get_current_username)])
async def view_page_html(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()
    if not row: return "<h1>Not found</h1>"
    content = html.escape(row[0])
    return f"<html><head><title>Page {page_id}</title></head><body><h1>Page {page_id}</h1><pre>{content}</pre><br><a href='/dashboard'>Back to Dashboard</a></body></html>"

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(get_current_username)])
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
        # Keep dashboard resilient even if a deployment has stale schema.
        ensure_phase1_schema()

        review = get_optimizer_review(
            limit=safe_health_limit,
            stale_days=safe_health_stale_days,
            confidence_threshold=safe_health_confidence,
            selected_scope=active_scope,
        )
        dry_run = get_optimizer_dry_run(
            limit=safe_health_limit,
            stale_days=safe_health_stale_days,
            confidence_threshold=safe_health_confidence,
            selected_scope=active_scope,
        )
        latest_manual_batch_id = get_latest_manual_archive_batch_id()
        version_info = get_version_info()
        update_status = get_update_status(fetch_remote=False)

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
                    tuple(list_scope_params),
                )
                selected_scope_total_count = int(cur.fetchone()[0] or 0)

                if query:
                    cur.execute(
                        f"SELECT id, content FROM pages WHERE content ILIKE %s{list_scope_clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                        tuple([f"%{query}%"] + list_scope_params + [per_page, offset]),
                    )
                    rows = cur.fetchall()
                    cur.execute(
                        f"SELECT COUNT(*) FROM pages WHERE content ILIKE %s{list_scope_clause}",
                        tuple([f"%{query}%"] + list_scope_params),
                    )
                    total_items = cur.fetchone()[0]
                else:
                    cur.execute(
                        f"SELECT id, content FROM pages WHERE 1=1{list_scope_clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                        tuple(list_scope_params + [per_page, offset]),
                    )
                    rows = cur.fetchall()
                    cur.execute(
                        f"SELECT COUNT(*) FROM pages WHERE 1=1{list_scope_clause}",
                        tuple(list_scope_params),
                    )
                    total_items = cur.fetchone()[0]
    except Exception as ex:
        return HTMLResponse(
            f"""
            <html><head><title>Dashboard Error</title></head>
            <body style='font-family: sans-serif; background:#121212; color:#fff; padding:24px;'>
                <h1>Dashboard Error</h1>
                <p>The dashboard failed to load due to a backend/runtime issue.</p>
                <pre style='white-space: pre-wrap; background:#1e1e1e; border:1px solid #333; padding:12px; border-radius:6px;'>{html.escape(str(ex))}</pre>
                <p>Common causes on VPS: database connectivity, missing DB schema, or environment mismatches.</p>
            </body></html>
            """,
            status_code=500,
        )

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

    watchdog_progress_text = "last_synced_id=n/a | latest_source_id=n/a"
    if watchdog_last_synced_id is not None and watchdog_latest_source_id is not None:
        watchdog_progress_text = (
            f"last_synced_id={int(watchdog_last_synced_id)} | "
            f"latest_source_id={int(watchdog_latest_source_id)}"
        )

    watchdog_error_html = ""
    if watchdog_last_error:
        watchdog_error_html = (
            "<div style='margin: 8px 0 0; padding: 10px; background:#3a1d1d; border:1px solid #8b2b2b; border-radius:6px; color:#fecaca;'>"
            f"Watchdog last_error: {html.escape(str(watchdog_last_error))}"
            "</div>"
        )

    watchdog_badge_html = "<span style='color:#9ca3af;'>Watchdog pending: n/a</span>"
    if watchdog_pending_value is not None:
        pending_int = max(0, int(watchdog_pending_value))
        if pending_int > 0:
            watchdog_badge_html = (
                "<span style='display:inline-flex; align-items:center; gap:8px; color:#22c55e; font-weight:700;'>"
                "<span style='width:10px; height:10px; border-radius:50%; background:#22c55e; display:inline-block; box-shadow:0 0 0 2px rgba(34,197,94,0.25);'></span>"
                f"Watchdog pending: {pending_int}"
                "</span>"
            )
        else:
            watchdog_badge_html = (
                "<span style='display:inline-flex; align-items:center; gap:8px; color:#93c5fd; font-weight:600;'>"
                "<span style='width:10px; height:10px; border-radius:50%; background:#93c5fd; display:inline-block;'></span>"
                "Watchdog pending: 0"
                "</span>"
            )

    total_pages = math.ceil(total_items / per_page)

    scope_options = [
        (DASHBOARD_SCOPE_ALL, "All users/scopes"),
        (DASHBOARD_SCOPE_UNSCOPED, "Unscoped (legacy rows)"),
    ]
    for scope_id, count in scope_rows:
        scope_options.append((scope_id, format_scope_label(str(scope_id), int(count))))

    if active_scope not in {opt[0] for opt in scope_options}:
        scope_options.append((active_scope, f"{format_scope_label(active_scope)} (selected)"))

    scope_select_html = ""
    for option_value, option_label in scope_options:
        selected_attr = " selected" if option_value == active_scope else ""
        scope_select_html += (
            f"<option value='{html.escape(str(option_value))}'{selected_attr}>"
            f"{html.escape(option_label)}"
            "</option>"
        )

    if active_scope == DASHBOARD_SCOPE_ALL:
        selected_scope_label = "All users/scopes"
    elif active_scope == DASHBOARD_SCOPE_UNSCOPED:
        selected_scope_label = "Unscoped (legacy rows)"
    else:
        selected_scope_label = format_scope_label(active_scope)
    
    items = ""
    for r in rows:
        items += f"""
        <li style="margin-bottom: 12px; background: #2a2a2a; padding: 10px; border-radius: 6px; border: 1px solid #444;">
            <a href='/page_html?page_id={r[0]}' style="color: #4da6ff; text-decoration: none;">[{r[0]}]</a>
            <span style="color: #ddd;">{html.escape(r[1][:80])}</span>
            <form action='/delete' method='post' style='display:inline; float:right;'>
                <input type='hidden' name='page_id' value='{r[0]}'>
                <button type='submit' style="background: #ff4d4d; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer;" onclick='return confirm("Delete?")'>Delete</button>
            </form>
            <form action='/tag_auto/{r[0]}' method='post' style='display:inline; float:right; margin-right: 10px;'>
                <button type='submit' style="background: #28a745; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer;">Auto-Tag</button>
            </form>
        </li>
        """

    nav = ""
    for i in range(1, total_pages + 1):
        active = "background: #4da6ff;" if i == page else "background: #333;"
        nav += f'<a href="/dashboard?page={i}&query={html.escape(query or "")}&selected_scope={quote_plus(active_scope)}&health_stale_days={safe_health_stale_days}&health_confidence_threshold={safe_health_confidence:.2f}&health_limit={safe_health_limit}" style="margin: 0 5px; padding: 5px 10px; color: white; text-decoration: none; border-radius: 4px; {active}">{i}</a>'

    pending_items = ""
    for item in review["pending_review"][:safe_health_limit]:
        pending_items += (
            f"<li style='margin: 6px 0; padding: 8px; border: 1px solid #444; border-radius: 6px;'>"
            f"<label style='display:block;'>"
            f"<input type='checkbox' name='selected_page_ids' value='{item['id']}' style='margin-right:8px;'>"
            f"<b>#{item['id']}</b> [{html.escape(item['memory_type'])}] "
            f"confidence={item['confidence']:.2f}, age={item['age_days']:.1f}d, reason={html.escape(item['review_reason'])}<br>"
            f"<span style='color:#bbb'>{html.escape(item['content'][:120])}</span>"
            f"</label>"
            f"</li>"
        )

    if not pending_items:
        pending_items = "<li style='color:#9ad29a;'>No pending stale/low-confidence memories.</li>"

    archived_items = ""
    for item in review["archived_recent"][:min(5, safe_health_limit)]:
        archived_items += (
            f"<li style='margin: 6px 0; padding: 8px; border: 1px solid #444; border-radius: 6px;'>"
            f"page_id={item['page_id']} reason={html.escape(item['archive_reason'])} "
            f"({item['days_since_archived']:.1f}d ago)"
            f"</li>"
        )

    if not archived_items:
        archived_items = "<li style='color:#bbb;'>No recent archives yet.</li>"

    optimizer_banner = ""
    if optimizer_msg:
        optimizer_banner = (
            f"<div style='margin: 14px 0; padding: 10px; background:#1f3b25; border:1px solid #2f6b3c; border-radius:6px;'>"
            f"{html.escape(optimizer_msg)}"
            f"</div>"
        )

    dry_run_banner = ""
    if dry_run_msg:
        dry_run_banner = (
            f"<div style='margin: 14px 0; padding: 10px; background:#2a2c11; border:1px solid #757b27; border-radius:6px;'>"
            f"{html.escape(dry_run_msg)}"
            f"</div>"
        )

    restore_banner = ""
    if restore_msg:
        restore_banner = (
            f"<div style='margin: 14px 0; padding: 10px; background:#1a2538; border:1px solid #3d5f8f; border-radius:6px;'>"
            f"{html.escape(restore_msg)}"
            f"</div>"
        )

    return f"""
    <html>
            <head><title>Memory Dashboard</title>
                <meta http-equiv="refresh" content="5">
        <style>body {{ font-family: sans-serif; background-color: #121212; color: #fff; padding: 20px; }} h1 {{ border-bottom: 2px solid #333; padding-bottom: 10px; }} input {{ padding: 8px; width: 300px; border-radius: 4px; border: 1px solid #444; background: #1e1e1e; color: white; }} button {{ padding: 8px 16px; background: #4da6ff; color: white; border: none; border-radius: 4px; cursor: pointer; }} ul {{ list-style-type: none; padding: 0; margin-top: 20px; }}</style>
      </head>
      <body>
                <h1>Memory Dashboard (Messages: {total_items} | {watchdog_badge_html})</h1>
                <p style="margin: 6px 0 4px; color:#bcd;">Watchdog status updated: {watchdog_updated_text}</p>
                <p style="margin: 0 0 14px; color:#9ec8ff;">{watchdog_progress_text}</p>
                {watchdog_error_html}
                {optimizer_banner}
            {dry_run_banner}
            {restore_banner}
        <form method="get" action="/dashboard">
          <input type="text" name="query" placeholder="Suche..." value="{html.escape(query or "")}">
                    <select name="selected_scope" style="margin-left:8px; padding:8px; border-radius:4px; border:1px solid #444; background:#1e1e1e; color:white; width:320px; max-width:100%;">
                        {scope_select_html}
                    </select>
                                        <span style="margin-left:8px; color:#bcd; font-size:0.9rem;">Selected user: {html.escape(selected_scope_label)} ({selected_scope_total_count} messages)</span>
                                        <span style="margin-left:8px; color:#9ca3af; font-size:0.85rem;">For real names set SCOPE_LABELS_JSON in .env.</span>
                    <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                    <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence:.2f}">
                    <input type="hidden" name="health_limit" value="{safe_health_limit}">
          <button type="submit">Search</button>
        </form>

                <div style="margin:20px 0; padding:12px; border:1px solid #355; border-radius:8px; background:#151f2b;">
                    <h2 style="margin-top:0;">Memory Health</h2>
                    <p style="margin:6px 0; color:#bcd;">App version: {html.escape(version_info['version'])}</p>
                    <p style="margin:6px 0; color:#bcd;">Update status: {"update available" if update_status.get('available') else "up to date"}</p>
                    <p style="margin:6px 0; color:#bcd;">Auto update enabled: {AUTO_UPDATE_ENABLED} | Auto apply: {AUTO_UPDATE_APPLY} | Interval: {AUTO_UPDATE_INTERVAL_SECONDS}s</p>
                    <p style="margin:6px 0; color:#bcd;">Pending review: {review['summary']['pending_count']} | Recently archived: {review['summary']['recent_archived_count']}</p>
                    <p style="margin:6px 0; color:#e6d992;">Dry-run forecast: would decay {dry_run['would_decay_count']} | would archive {dry_run['would_archive_count']}</p>

                    <form method="post" action="/update/run_from_dashboard" style="margin: 10px 0;">
                        <input type="hidden" name="query" value="{html.escape(query or '')}">
                        <input type="hidden" name="selected_scope" value="{html.escape(active_scope)}">
                        <input type="hidden" name="page" value="{page}">
                        <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                        <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                        <input type="hidden" name="health_limit" value="{safe_health_limit}">
                        <button type="submit" style="background:#4e8f6c; color:#fff;">Run App Update</button>
                    </form>

                    <p style="margin:8px 0;">
                        <a style="color:#9ec8ff;" href="/version">Open Version JSON</a>
                        &nbsp;|&nbsp;
                        <a style="color:#9ec8ff;" href="/update/status">Open Update Status JSON</a>
                    </p>

                        <form method="get" action="/dashboard" style="margin: 10px 0; display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
                            <input type="hidden" name="page" value="{page}">
                            <input type="hidden" name="query" value="{html.escape(query or '')}">
                            <input type="hidden" name="selected_scope" value="{html.escape(active_scope)}">
                            <label style="font-size:0.9rem;">Stale days:
                                <input type="number" min="1" max="3650" name="health_stale_days" value="{safe_health_stale_days}" style="width:90px; margin-left:6px;">
                            </label>
                            <label style="font-size:0.9rem;">Confidence threshold:
                                <input type="number" step="0.01" min="0" max="1" name="health_confidence_threshold" value="{safe_health_confidence:.2f}" style="width:90px; margin-left:6px;">
                            </label>
                            <label style="font-size:0.9rem;">Rows:
                                <input type="number" min="1" max="50" name="health_limit" value="{safe_health_limit}" style="width:70px; margin-left:6px;">
                            </label>
                            <button type="submit" style="background:#3a78d4;">Apply Filters</button>
                        </form>

                    <form method="post" action="/optimizer/run_from_dashboard" style="margin: 10px 0;">
                            <input type="hidden" name="query" value="{html.escape(query or '')}">
                            <input type="hidden" name="selected_scope" value="{html.escape(active_scope)}">
                            <input type="hidden" name="page" value="{page}">
                            <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                            <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                            <input type="hidden" name="health_limit" value="{safe_health_limit}">
                        <button type="submit" style="background:#f0b429; color:#111;">Run Optimizer Now</button>
                    </form>

                    <form method="post" action="/optimizer/dry_run_from_dashboard" style="margin: 10px 0;">
                        <input type="hidden" name="query" value="{html.escape(query or '')}">
                        <input type="hidden" name="selected_scope" value="{html.escape(active_scope)}">
                        <input type="hidden" name="page" value="{page}">
                        <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                        <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                        <input type="hidden" name="health_limit" value="{safe_health_limit}">
                        <button type="submit" style="background:#9aa3af; color:#111;">Preview Dry Run</button>
                    </form>

                    <form method="post" action="/optimizer/undo_latest_manual_archive_from_dashboard" style="margin: 10px 0;">
                        <input type="hidden" name="query" value="{html.escape(query or '')}">
                        <input type="hidden" name="selected_scope" value="{html.escape(active_scope)}">
                        <input type="hidden" name="page" value="{page}">
                        <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                        <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                        <input type="hidden" name="health_limit" value="{safe_health_limit}">
                        <button type="submit" style="background:#5e77a8; color:#fff;" {"" if latest_manual_batch_id else "disabled"}>Undo Last Manual Archive</button>
                    </form>
                    <p style="margin:4px 0; color:#b5c4df; font-size:0.9rem;">Latest manual batch: {html.escape(latest_manual_batch_id or 'none')}</p>

                        <p style="margin:8px 0;">
                            <a style="color:#9ec8ff;" href="/optimizer/review?limit={safe_health_limit}&stale_days={safe_health_stale_days}&confidence_threshold={safe_health_confidence:.2f}&selected_scope={quote_plus(active_scope)}">Open Raw Review JSON</a>
                        </p>

                    <div style="display:flex; gap:16px; flex-wrap:wrap;">
                        <div style="flex:1; min-width:320px;">
                            <h3 style="margin:8px 0;">Pending Review</h3>
                            <form method="post" action="/optimizer/archive_selected_from_dashboard" style="margin:0;">
                                <input type="hidden" name="query" value="{html.escape(query or '')}">
                                <input type="hidden" name="selected_scope" value="{html.escape(active_scope)}">
                                <input type="hidden" name="page" value="{page}">
                                <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                                <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                                <input type="hidden" name="health_limit" value="{safe_health_limit}">
                                <ul style="margin-top:0;">{pending_items}</ul>
                                <button type="submit" style="background:#b85c3b; margin-top:8px;">Archive Selected</button>
                            </form>
                        </div>
                        <div style="flex:1; min-width:320px;">
                            <h3 style="margin:8px 0;">Recently Archived</h3>
                            <ul style="margin-top:0;">{archived_items}</ul>
                        </div>
                    </div>
                </div>

        <div style="margin: 20px 0;">{nav}</div>
        <ul>{items}</ul>
        <div style="margin: 20px 0;">{nav}</div>
      </body>
    </html>
    """

# ---------------------------------------------------------
#  STARTUP & RUN
# ---------------------------------------------------------
def start_sync():
    threading.Thread(target=_supervised_sync_loop, daemon=True).start()


def _supervised_sync_loop() -> None:
    """Run memory_sync.run_sync with crash detection and automatic restart."""
    import memory_sync
    global SYNC_LIVENESS
    while True:
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
        time.sleep(5)


def start_decay_optimizer():
    threading.Thread(target=decay_loop, daemon=True).start()


def start_auto_update_worker():
    if AUTO_UPDATE_ENABLED:
        threading.Thread(target=auto_update_loop, daemon=True).start()

@app.on_event("startup")
def startup_event():
    validate_security_startup()

    try:
        ensure_phase1_schema()
        orphaned_deleted = cleanup_orphaned_embeddings()
        print(f"[SCHEMA] cleanup_orphaned_embeddings deleted={orphaned_deleted}")
        run_decay_and_archive_once()
    except Exception as ex:
        raise RuntimeError(f"Startup failed during database initialization: {ex}") from ex

    start_decay_optimizer()
    start_auto_update_worker()
    start_sync()


@app.on_event("shutdown")
def shutdown_event():
    close_db_pool()


@app.post("/optimizer/run", dependencies=[Depends(get_current_username)])
async def run_optimizer_now():
    stats = await run_in_threadpool(run_decay_and_archive_once)
    return {"status": "ok", "optimizer": stats}


@app.get("/version")
async def version_info():
    return {
        "status": "ok",
        "version": get_version_info(),
        "providers": provider_runtime_info(),
    }


@app.get("/update/status", dependencies=[Depends(get_current_username)])
async def update_status():
    return {"status": "ok", "update": await run_in_threadpool(get_update_status, True)}


@app.post("/update/run", dependencies=[Depends(get_current_username)])
async def update_run():
    result = await run_in_threadpool(run_update)
    return {"status": "ok", "update": result}


@app.post("/update/run_from_dashboard", dependencies=[Depends(get_current_username)])
async def update_run_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))
    result = await run_in_threadpool(run_update)
    if result.get("updated"):
        msg = "Update run complete."
    else:
        msg = result.get("message", "No update applied.")
    return RedirectResponse(
        url=(
            f"/dashboard?optimizer_msg={quote_plus(msg)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&selected_scope={quote_plus((selected_scope or DASHBOARD_SCOPE_ALL).strip() or DASHBOARD_SCOPE_ALL)}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.post("/optimizer/archive_selected", dependencies=[Depends(get_current_username)])
async def optimizer_archive_selected(payload: ArchiveSelectionRequest):
    result = await run_in_threadpool(
        lambda: archive_selected_pages(payload.page_ids, archive_reason=payload.archive_reason)
    )
    return {"status": "ok", "archive": result}


@app.post("/optimizer/undo_latest_manual_archive", dependencies=[Depends(get_current_username)])
async def optimizer_undo_latest_manual_archive():
    latest_batch_id = await run_in_threadpool(get_latest_manual_archive_batch_id)
    if not latest_batch_id:
        return {"status": "ok", "undo": {"restored": 0, "deleted_from_archive": 0, "archive_batch_id": None}}
    result = await run_in_threadpool(restore_archive_batch, latest_batch_id)
    return {"status": "ok", "undo": result}


@app.post("/optimizer/run_from_dashboard", dependencies=[Depends(get_current_username)])
async def run_optimizer_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    stats = await run_in_threadpool(run_decay_and_archive_once)
    message = f"Optimizer completed: decayed={stats['decayed']}, archived={stats['archived']}, deleted={stats['deleted']}"
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))
    return RedirectResponse(
        url=(
            f"/dashboard?optimizer_msg={quote_plus(message)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&selected_scope={quote_plus((selected_scope or DASHBOARD_SCOPE_ALL).strip() or DASHBOARD_SCOPE_ALL)}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.get("/optimizer/dry_run", dependencies=[Depends(get_current_username)])
async def optimizer_dry_run(
    limit: int = 25,
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    selected_scope: str = DASHBOARD_SCOPE_ALL,
):
    safe_limit = max(1, min(limit, 200))
    safe_stale_days = max(1, min(stale_days, 3650))
    safe_confidence = clamp(confidence_threshold, 0.0, 1.0)
    dry_run = await run_in_threadpool(
        lambda: get_optimizer_dry_run(
            limit=safe_limit,
            stale_days=safe_stale_days,
            confidence_threshold=safe_confidence,
            selected_scope=selected_scope,
        )
    )
    return {"status": "ok", "dry_run": dry_run}


@app.post("/optimizer/dry_run_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_dry_run_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))
    dry_run = await run_in_threadpool(
        lambda: get_optimizer_dry_run(
            limit=safe_limit,
            stale_days=safe_stale_days,
            confidence_threshold=safe_confidence,
            selected_scope=selected_scope,
        )
    )
    dry_message = (
        f"Dry run preview: would decay={dry_run['would_decay_count']}, "
        f"would archive={dry_run['would_archive_count']}"
    )
    return RedirectResponse(
        url=(
            f"/dashboard?dry_run_msg={quote_plus(dry_message)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&selected_scope={quote_plus((selected_scope or DASHBOARD_SCOPE_ALL).strip() or DASHBOARD_SCOPE_ALL)}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.post("/optimizer/archive_selected_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_archive_selected_from_dashboard(
    selected_page_ids: list[int] = Form([]),
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))

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

    return RedirectResponse(
        url=(
            f"/dashboard?{msg_key}={quote_plus(msg)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&selected_scope={quote_plus((selected_scope or DASHBOARD_SCOPE_ALL).strip() or DASHBOARD_SCOPE_ALL)}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.post("/optimizer/undo_latest_manual_archive_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_undo_latest_manual_archive_from_dashboard(
    query: str = Form(""),
    selected_scope: str = Form(DASHBOARD_SCOPE_ALL),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))

    latest_batch_id = await run_in_threadpool(get_latest_manual_archive_batch_id)
    if not latest_batch_id:
        msg = "No manual archive batch available to undo."
    else:
        result = await run_in_threadpool(restore_archive_batch, latest_batch_id)
        msg = (
            f"Undo complete: restored={result['restored']}, "
            f"removed_archive_rows={result['deleted_from_archive']}, batch={result['archive_batch_id']}"
        )

    return RedirectResponse(
        url=(
            f"/dashboard?restore_msg={quote_plus(msg)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&selected_scope={quote_plus((selected_scope or DASHBOARD_SCOPE_ALL).strip() or DASHBOARD_SCOPE_ALL)}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.get("/optimizer/review", dependencies=[Depends(get_current_username)])
async def review_optimizer_candidates(
    limit: int = 25,
    stale_days: int = 14,
    confidence_threshold: float = 0.3,
    selected_scope: str = DASHBOARD_SCOPE_ALL,
):
    safe_limit = max(1, min(limit, 200))
    safe_stale_days = max(1, min(stale_days, 3650))
    safe_confidence = clamp(confidence_threshold, 0.0, 1.0)
    review = await run_in_threadpool(
        lambda: get_optimizer_review(
            limit=safe_limit,
            stale_days=safe_stale_days,
            confidence_threshold=safe_confidence,
            selected_scope=selected_scope,
        )
    )
    return {"status": "ok", "review": review}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
