"""Scoring: memory classification, sentiment, hybrid search score."""

import math
from hermesclaw.config import SCOPE_ALIASES, DASHBOARD_SCOPE_ALL, DASHBOARD_SCOPE_UNSCOPED


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


# ---------------------------------------------------------------------------
# Scope / chat ID helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Hybrid search scoring
# ---------------------------------------------------------------------------
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
