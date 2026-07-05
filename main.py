from fastapi import Body, FastAPI, HTTPException, Depends, status, UploadFile, File, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import psycopg
import ollama
import requests
import os
import threading
import html
import secrets
import math
import shutil
import time
import json
import base64
import hashlib
import hmac
from urllib.parse import quote_plus
import uuid
import subprocess
import tempfile
from pathlib import Path
import whisper
from pydantic import BaseModel

app = FastAPI()

# Security
security = HTTPBasic(auto_error=False)
API_KEY = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
DASHBOARD_SESSION_COOKIE = "dashboard_session"
DASHBOARD_SESSION_TTL_SECONDS = int(os.getenv("DASHBOARD_SESSION_TTL_SECONDS", "43200"))
DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET") or API_KEY or "change-this-dashboard-secret"


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

# AI Setup
whisper_model = whisper.load_model("base")

class CaptureRequest(BaseModel):
    text: str


class ArchiveSelectionRequest(BaseModel):
    page_ids: list[int]
    archive_reason: str = "manual_review"

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

    status_text = "ok" if db_ok else "degraded"
    payload = {
        "status": status_text,
        "database": "ok" if db_ok else "error",
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


def connect_db():
    conn_kwargs = {"host": DB_HOST, "port": DB_PORT, "dbname": DB_NAME, "user": DB_USER, "password": DB_PASSWORD}
    try: return psycopg.connect(**conn_kwargs)
    except psycopg.OperationalError: raise


def embedding_to_pgvector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11435")
client = ollama.Client(host=OLLAMA_HOST)
AI_PROVIDER = (os.getenv("AI_PROVIDER", "ollama") or "ollama").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
OPENROUTER_EMBED_MODEL = os.getenv("OPENROUTER_EMBED_MODEL", "text-embedding-3-small")
DECAY_INTERVAL_SECONDS = int(os.getenv("MEMORY_DECAY_INTERVAL_SECONDS", "21600"))


def generate_embedding(text: str) -> list[float]:
    provider = AI_PROVIDER
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
            raise HTTPException(status_code=500, detail=f"OpenAI embedding failed: {response.text}")
        data = response.json()
        return data["data"][0]["embedding"]

    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is required when AI_PROVIDER=openrouter")
        response = requests.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": OPENROUTER_EMBED_MODEL, "input": text},
            timeout=45,
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=500, detail=f"OpenRouter embedding failed: {response.text}")
        data = response.json()
        return data["data"][0]["embedding"]

    raise HTTPException(
        status_code=500,
        detail=(
            "Unsupported AI_PROVIDER for embeddings. "
            "Use one of: ollama, openai, openrouter"
        ),
    )


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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_archive_batch ON pages_archive(archive_batch_id)")
            conn.commit()


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


def get_optimizer_review(limit: int = 25, stale_days: int = 14, confidence_threshold: float = 0.3) -> dict:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
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
                  AND (
                        p.confidence < %s
                        OR COALESCE(p.last_used, p.created_at) < NOW() - (%s || ' days')::interval
                        OR (p.ttl_days IS NOT NULL AND p.created_at + (p.ttl_days || ' days')::interval < NOW())
                  )
                ORDER BY p.confidence ASC, age_days DESC
                LIMIT %s
                """,
                (confidence_threshold, stale_days, confidence_threshold, stale_days, limit),
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


def get_optimizer_dry_run(stale_days: int = 14, confidence_threshold: float = 0.3, limit: int = 25) -> dict:
        with connect_db() as conn:
                with conn.cursor() as cur:
                        cur.execute(
                                """
                                SELECT COUNT(*)
                                FROM pages
                                WHERE is_archived = FALSE
                                    AND last_used < NOW() - INTERVAL '7 days'
                                """
                        )
                        would_decay_count = int(cur.fetchone()[0])

                        cur.execute(
                                """
                                SELECT COUNT(*)
                                FROM pages
                                WHERE is_archived = FALSE
                                    AND (
                                        (ttl_days IS NOT NULL AND created_at + (ttl_days || ' days')::interval < NOW())
                                        OR confidence < 0.18
                                    )
                                """
                        )
                        would_archive_count = int(cur.fetchone()[0])

                        cur.execute(
                                """
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
                                (confidence_threshold, stale_days, limit),
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
                    print(f"[UPDATE] auto-apply result: {result.get('updated')}")
        except Exception as ex:
            print(f"[UPDATE] loop error: {ex}")
        time.sleep(AUTO_UPDATE_INTERVAL_SECONDS)


def decay_loop() -> None:
    while True:
        try:
            stats = run_decay_and_archive_once()
            print(f"[OPTIMIZER] decay={stats['decayed']} archived={stats['archived']} deleted={stats['deleted']}")
        except Exception as ex:
            print(f"[OPTIMIZER] error: {ex}")
        time.sleep(DECAY_INTERVAL_SECONDS)


# ---------------------------------------------------------
#  CAPTURE & SEARCH
# ---------------------------------------------------------
def find_similar_page(text: str, threshold: float = 0.05):
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
                ORDER BY dist ASC
                LIMIT 1
                """,
                (emb_str,),
            )
            row = cur.fetchone()
    if row and row[2] <= threshold: return {"id": row[0], "content": row[1], "distance": row[2]}
    return None

@app.post("/capture")
async def capture(text: str | None = None, body: CaptureRequest | None = Body(default=None)):
    capture_text = text if text is not None else (body.text if body else None)
    if not capture_text or not capture_text.strip(): raise HTTPException(status_code=400, detail="text is required")
    capture_text = capture_text.strip()
    meta = score_memory(capture_text)
    similar = find_similar_page(capture_text)
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
                    sentiment, source, ttl_days, updated_at, last_used
                )
                VALUES (%s, %s, %s, %s, 1, %s, %s, %s, NOW(), NOW())
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
                ),
            )
            page_id = cur.fetchone()[0]
            conn.commit()

    emb = generate_embedding(capture_text)
    emb_str = embedding_to_pgvector_literal(emb)
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)", (page_id, emb_str))
            conn.commit()
    return {
        "status": "ok",
        "page_id": page_id,
        "memory_type": meta["memory_type"],
        "score": meta["score"],
        "importance": round(meta["importance"], 3),
        "confidence": round(meta["confidence"], 3),
    }

@app.post("/transcribe", dependencies=[Depends(require_api_key)])
async def transcribe(file: UploadFile = File(...)):
    suffix = Path(file.filename or "audio").suffix
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            shutil.copyfileobj(file.file, temp_file)

        result = whisper_model.transcribe(temp_path)
        return {"text": result["text"]}
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

@app.get("/search")
async def search(query: str = "", limit: int = 5, rerank_results: bool = False):
    if query.strip() == "":
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, memory_type, importance, confidence, frequency
                    FROM pages
                    WHERE is_archived = FALSE
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (limit,),
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
    
    qemb = generate_embedding(query)
    qemb_str = embedding_to_pgvector_literal(qemb)

    candidates: dict[int, dict] = {}

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                       EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0 AS age_days,
                       e.embedding <-> %s::vector AS vector_distance
                FROM embeddings e
                JOIN pages p ON p.id = e.page_id
                WHERE p.is_archived = FALSE
                ORDER BY vector_distance
                LIMIT %s
                """,
                (qemb_str, limit * 2),
            )
            vector_rows = cur.fetchall()

            cur.execute(
                """
                SELECT p.id, p.content, p.memory_type, p.importance, p.confidence, p.frequency,
                       EXTRACT(EPOCH FROM (NOW() - COALESCE(p.last_retrieved, p.last_used, p.created_at))) / 86400.0 AS age_days,
                       ts_rank_cd(to_tsvector('english', p.content), plainto_tsquery('english', %s)) AS lexical_rank
                FROM pages p
                WHERE p.is_archived = FALSE
                  AND to_tsvector('english', p.content) @@ plainto_tsquery('english', %s)
                ORDER BY lexical_rank DESC
                LIMIT %s
                """,
                (query, query, limit * 2),
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
        }
        for r in results[:limit]
    ]

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
    except:
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
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, content FROM pages")
            rows = cur.fetchall()
    return [{"id": r[0], "content": r[1]} for r in rows]

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
    try:
        # Keep dashboard resilient even if a deployment has stale schema.
        ensure_phase1_schema()

        review = get_optimizer_review(
            limit=safe_health_limit,
            stale_days=safe_health_stale_days,
            confidence_threshold=safe_health_confidence,
        )
        dry_run = get_optimizer_dry_run(
            limit=safe_health_limit,
            stale_days=safe_health_stale_days,
            confidence_threshold=safe_health_confidence,
        )
        latest_manual_batch_id = get_latest_manual_archive_batch_id()
        version_info = get_version_info()
        update_status = get_update_status(fetch_remote=False)

        with connect_db() as conn:
            with conn.cursor() as cur:
                if query:
                    cur.execute("SELECT id, content FROM pages WHERE content ILIKE %s ORDER BY id DESC LIMIT %s OFFSET %s", (f"%{query}%", per_page, offset))
                    rows = cur.fetchall()
                    cur.execute("SELECT COUNT(*) FROM pages WHERE content ILIKE %s", (f"%{query}%",))
                    total_items = cur.fetchone()[0]
                else:
                    cur.execute("SELECT id, content FROM pages ORDER BY id DESC LIMIT %s OFFSET %s", (per_page, offset))
                    rows = cur.fetchall()
                    cur.execute("SELECT COUNT(*) FROM pages")
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

    total_pages = math.ceil(total_items / per_page)
    
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
        nav += f'<a href="/dashboard?page={i}&query={html.escape(query or "")}" style="margin: 0 5px; padding: 5px 10px; color: white; text-decoration: none; border-radius: 4px; {active}">{i}</a>'

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
        <style>body {{ font-family: sans-serif; background-color: #121212; color: #fff; padding: 20px; }} h1 {{ border-bottom: 2px solid #333; padding-bottom: 10px; }} input {{ padding: 8px; width: 300px; border-radius: 4px; border: 1px solid #444; background: #1e1e1e; color: white; }} button {{ padding: 8px 16px; background: #4da6ff; color: white; border: none; border-radius: 4px; cursor: pointer; }} ul {{ list-style-type: none; padding: 0; margin-top: 20px; }}</style>
      </head>
      <body>
        <h1>Memory Dashboard (Page {page})</h1>
                {optimizer_banner}
            {dry_run_banner}
            {restore_banner}
        <form method="get" action="/dashboard">
          <input type="text" name="query" placeholder="Suche..." value="{html.escape(query or "")}">
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
                            <input type="hidden" name="page" value="{page}">
                            <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                            <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                            <input type="hidden" name="health_limit" value="{safe_health_limit}">
                        <button type="submit" style="background:#f0b429; color:#111;">Run Optimizer Now</button>
                    </form>

                    <form method="post" action="/optimizer/dry_run_from_dashboard" style="margin: 10px 0;">
                        <input type="hidden" name="query" value="{html.escape(query or '')}">
                        <input type="hidden" name="page" value="{page}">
                        <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                        <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                        <input type="hidden" name="health_limit" value="{safe_health_limit}">
                        <button type="submit" style="background:#9aa3af; color:#111;">Preview Dry Run</button>
                    </form>

                    <form method="post" action="/optimizer/undo_latest_manual_archive_from_dashboard" style="margin: 10px 0;">
                        <input type="hidden" name="query" value="{html.escape(query or '')}">
                        <input type="hidden" name="page" value="{page}">
                        <input type="hidden" name="health_stale_days" value="{safe_health_stale_days}">
                        <input type="hidden" name="health_confidence_threshold" value="{safe_health_confidence}">
                        <input type="hidden" name="health_limit" value="{safe_health_limit}">
                        <button type="submit" style="background:#5e77a8; color:#fff;" {"" if latest_manual_batch_id else "disabled"}>Undo Last Manual Archive</button>
                    </form>
                    <p style="margin:4px 0; color:#b5c4df; font-size:0.9rem;">Latest manual batch: {html.escape(latest_manual_batch_id or 'none')}</p>

                        <p style="margin:8px 0;">
                            <a style="color:#9ec8ff;" href="/optimizer/review?limit={safe_health_limit}&stale_days={safe_health_stale_days}&confidence_threshold={safe_health_confidence:.2f}">Open Raw Review JSON</a>
                        </p>

                    <div style="display:flex; gap:16px; flex-wrap:wrap;">
                        <div style="flex:1; min-width:320px;">
                            <h3 style="margin:8px 0;">Pending Review</h3>
                            <form method="post" action="/optimizer/archive_selected_from_dashboard" style="margin:0;">
                                <input type="hidden" name="query" value="{html.escape(query or '')}">
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
    import memory_sync
    threading.Thread(target=memory_sync.run_sync, daemon=True).start()


def start_decay_optimizer():
    threading.Thread(target=decay_loop, daemon=True).start()


def start_auto_update_worker():
    if AUTO_UPDATE_ENABLED:
        threading.Thread(target=auto_update_loop, daemon=True).start()

@app.on_event("startup")
def startup_event():
    if not API_KEY:
        raise RuntimeError("API_KEY is required.")
    if DASHBOARD_PASSWORD == "admin":
        print("[SECURITY] DASHBOARD_PASSWORD is still set to default 'admin'.")

    try:
        ensure_phase1_schema()
        run_decay_and_archive_once()
    except Exception as ex:
        raise RuntimeError(f"Startup failed during database initialization: {ex}") from ex

    start_decay_optimizer()
    start_auto_update_worker()
    start_sync()


@app.post("/optimizer/run", dependencies=[Depends(get_current_username)])
async def run_optimizer_now():
    stats = run_decay_and_archive_once()
    return {"status": "ok", "optimizer": stats}


@app.get("/version")
async def version_info():
    return {"status": "ok", "version": get_version_info()}


@app.get("/update/status", dependencies=[Depends(get_current_username)])
async def update_status():
    return {"status": "ok", "update": get_update_status(fetch_remote=True)}


@app.post("/update/run", dependencies=[Depends(get_current_username)])
async def update_run():
    result = run_update()
    return {"status": "ok", "update": result}


@app.post("/update/run_from_dashboard", dependencies=[Depends(get_current_username)])
async def update_run_from_dashboard(
    query: str = Form(""),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))
    result = run_update()
    if result.get("updated"):
        msg = "Update run complete."
    else:
        msg = result.get("message", "No update applied.")
    return RedirectResponse(
        url=(
            f"/dashboard?optimizer_msg={quote_plus(msg)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.post("/optimizer/archive_selected", dependencies=[Depends(get_current_username)])
async def optimizer_archive_selected(payload: ArchiveSelectionRequest):
    result = archive_selected_pages(payload.page_ids, archive_reason=payload.archive_reason)
    return {"status": "ok", "archive": result}


@app.post("/optimizer/undo_latest_manual_archive", dependencies=[Depends(get_current_username)])
async def optimizer_undo_latest_manual_archive():
    latest_batch_id = get_latest_manual_archive_batch_id()
    if not latest_batch_id:
        return {"status": "ok", "undo": {"restored": 0, "deleted_from_archive": 0, "archive_batch_id": None}}
    result = restore_archive_batch(latest_batch_id)
    return {"status": "ok", "undo": result}


@app.post("/optimizer/run_from_dashboard", dependencies=[Depends(get_current_username)])
async def run_optimizer_from_dashboard(
    query: str = Form(""),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    stats = run_decay_and_archive_once()
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
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.get("/optimizer/dry_run", dependencies=[Depends(get_current_username)])
async def optimizer_dry_run(limit: int = 25, stale_days: int = 14, confidence_threshold: float = 0.3):
    safe_limit = max(1, min(limit, 200))
    safe_stale_days = max(1, min(stale_days, 3650))
    safe_confidence = clamp(confidence_threshold, 0.0, 1.0)
    dry_run = get_optimizer_dry_run(
        limit=safe_limit,
        stale_days=safe_stale_days,
        confidence_threshold=safe_confidence,
    )
    return {"status": "ok", "dry_run": dry_run}


@app.post("/optimizer/dry_run_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_dry_run_from_dashboard(
    query: str = Form(""),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))
    dry_run = get_optimizer_dry_run(
        limit=safe_limit,
        stale_days=safe_stale_days,
        confidence_threshold=safe_confidence,
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
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))

    result = archive_selected_pages(selected_page_ids, archive_reason="manual_selected")
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
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.post("/optimizer/undo_latest_manual_archive_from_dashboard", dependencies=[Depends(get_current_username)])
async def optimizer_undo_latest_manual_archive_from_dashboard(
    query: str = Form(""),
    page: int = Form(1),
    health_stale_days: int = Form(14),
    health_confidence_threshold: float = Form(0.3),
    health_limit: int = Form(8),
):
    safe_page = max(1, page)
    safe_stale_days = max(1, min(health_stale_days, 3650))
    safe_confidence = clamp(health_confidence_threshold, 0.0, 1.0)
    safe_limit = max(1, min(health_limit, 50))

    latest_batch_id = get_latest_manual_archive_batch_id()
    if not latest_batch_id:
        msg = "No manual archive batch available to undo."
    else:
        result = restore_archive_batch(latest_batch_id)
        msg = (
            f"Undo complete: restored={result['restored']}, "
            f"removed_archive_rows={result['deleted_from_archive']}, batch={result['archive_batch_id']}"
        )

    return RedirectResponse(
        url=(
            f"/dashboard?restore_msg={quote_plus(msg)}"
            f"&page={safe_page}"
            f"&query={quote_plus(query or '')}"
            f"&health_stale_days={safe_stale_days}"
            f"&health_confidence_threshold={safe_confidence:.2f}"
            f"&health_limit={safe_limit}"
        ),
        status_code=303,
    )


@app.get("/optimizer/review", dependencies=[Depends(get_current_username)])
async def review_optimizer_candidates(limit: int = 25, stale_days: int = 14, confidence_threshold: float = 0.3):
    safe_limit = max(1, min(limit, 200))
    safe_stale_days = max(1, min(stale_days, 3650))
    safe_confidence = clamp(confidence_threshold, 0.0, 1.0)
    review = get_optimizer_review(
        limit=safe_limit,
        stale_days=safe_stale_days,
        confidence_threshold=safe_confidence,
    )
    return {"status": "ok", "review": review}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
