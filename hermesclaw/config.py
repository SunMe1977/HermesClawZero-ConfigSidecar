"""Environment config, constants, and env helpers."""

import json
import os


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


# Security
API_KEY = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
DASHBOARD_SESSION_COOKIE = "dashboard_session"
DASHBOARD_SESSION_TTL_SECONDS = int(os.getenv("DASHBOARD_SESSION_TTL_SECONDS", "43200"))
DASHBOARD_SESSION_SECRET = (os.getenv("DASHBOARD_SESSION_SECRET", "") or "").strip()

# Database
DB_HOST = os.getenv("DB_HOST", "gbrain-postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "gbrain")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Embedding providers
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11435")
AI_PROVIDER = (os.getenv("AI_PROVIDER", "ollama") or "ollama").strip().lower()
EMBEDDING_PROVIDER = (os.getenv("EMBEDDING_PROVIDER", "auto") or "auto").strip().lower()
ALLOW_EMBEDDING_SCHEMA_RESET = env_bool("ALLOW_EMBEDDING_SCHEMA_RESET", False)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
OPENROUTER_EMBED_MODEL = os.getenv("OPENROUTER_EMBED_MODEL", "text-embedding-3-small")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "models/text-embedding-004")
EMBEDDING_DIM = os.getenv("EMBEDDING_DIM", "").strip()

# Decay / optimizer
DECAY_INTERVAL_SECONDS = int(os.getenv("MEMORY_DECAY_INTERVAL_SECONDS", "21600"))

# Rate limiting
RATE_LIMIT_RULES: dict[str, tuple[int, int]] = {
    "/capture": (30, 60),
    "/search": (60, 60),
}

# OpenRouter retry
OPENROUTER_MAX_RETRIES = 3
OPENROUTER_RETRY_BASE_SECONDS = 1.0
OPENROUTER_DEGRADED_MESSAGE = (
    "OpenRouter temporarily unavailable. Memories are saved but search will be degraded."
)

# Dashboard scope constants
DASHBOARD_SCOPE_ALL = "all"
DASHBOARD_SCOPE_UNSCOPED = "__unscoped__"
SCOPE_LABELS_JSON = (os.getenv("SCOPE_LABELS_JSON", "") or "").strip()
SCOPE_ALIASES = _load_scope_aliases(SCOPE_LABELS_JSON)

# Git / update
REPO_DIR = os.getenv("UPDATE_REPO_DIR", os.getcwd())
UPDATE_REMOTE = os.getenv("AUTO_UPDATE_REMOTE", "origin")
UPDATE_BRANCH = os.getenv("AUTO_UPDATE_BRANCH", "main")
AUTO_UPDATE_ENABLED = env_bool("AUTO_UPDATE_ENABLED", True)
AUTO_UPDATE_APPLY = env_bool("AUTO_UPDATE_APPLY", True)
AUTO_UPDATE_INTERVAL_SECONDS = max(60, int(os.getenv("AUTO_UPDATE_INTERVAL_MINUTES", "60")) * 60)
UPDATE_RESTART_COMMAND = os.getenv("UPDATE_RESTART_COMMAND", "")
