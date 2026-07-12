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
    """Seven-term hybrid score: vector + lexical + retention + importance + recency + frequency + staleness."""
    vector_component = normalize_vector_distance(item.get("vector_distance"))
    lexical_component = normalize_lexical_rank(item.get("lexical_rank"))
    importance_component = clamp(float(item.get("importance") or 0.5))
    confidence_component = clamp(float(item.get("confidence") or 0.5))
    recency_component = normalize_recency(item.get("age_days"))
    frequency_component = normalize_frequency(item.get("frequency"))
    retention_component = clamp(item.get("retention") or 1.0)
    staleness_component = clamp(item.get("staleness_penalty") or 0.0)

    base_score = (
        0.30 * vector_component
        + 0.15 * lexical_component
        + 0.15 * retention_component
        + 0.12 * importance_component
        + 0.10 * recency_component
        + 0.08 * frequency_component
        - 0.10 * staleness_component
    )
    final_score = round(base_score * (0.5 + 0.5 * confidence_component), 6)

    explain = {
        "components": {
            "vector": round(vector_component, 4),
            "lexical": round(lexical_component, 4),
            "retention": round(retention_component, 4),
            "importance": round(importance_component, 4),
            "confidence": round(confidence_component, 4),
            "recency": round(recency_component, 4),
            "frequency": round(frequency_component, 4),
            "staleness": round(staleness_component, 4),
        },
        "weights": {
            "vector": 0.30,
            "lexical": 0.15,
            "retention": 0.15,
            "importance": 0.12,
            "recency": 0.10,
            "frequency": 0.08,
            "staleness_penalty": -0.10,
        },
    }

    reasons = []
    if lexical_component >= 0.35:
        reasons.append("strong keyword overlap")
    if vector_component >= 0.6:
        reasons.append("high semantic similarity")
    if retention_component >= 0.7:
        reasons.append("fresh in memory (Ebbinghaus)")
    if importance_component >= 0.75:
        reasons.append("high importance memory")
    if recency_component >= 0.6:
        reasons.append("recently used")
    if frequency_component >= 0.5:
        reasons.append("frequently retrieved")

    explain["reasons"] = reasons or ["balanced hybrid match"]
    explain["final_score"] = final_score
    return final_score, explain


# ---------------------------------------------------------------------------
# Ebbinghaus forgetting-curve — stability, retention, reinforcement
# ---------------------------------------------------------------------------
_ALPHA = 0.3  # stability growth rate (spacing effect)
_STABILITY_FLOOR = 0.5


def retention_score(stability: float, last_access: float | None, now: float | None = None) -> float:
    """Ebbinghaus R(t) = exp(-Δt_days / S).

    Returns 1.0 for brand-new memories, decays toward 0 as time since
    last access exceeds stability.
    """
    import time as _time
    now = now or _time.time()
    S = max(stability or 1.0, 0.01)
    dt = max((now - (last_access if last_access else now)) / 86400.0, 0.0)
    return math.exp(-dt / S)


def update_stability(current_stability: float, access_count: int) -> float:
    """Stability grows via the spacing effect: S_new = S * (1 + α * log(1 + n))."""
    return max(current_stability * (1.0 + _ALPHA * math.log(1 + access_count)), _STABILITY_FLOOR)


INTERACTION_BOOST = {
    "capture": 1.0,
    "retrieve": 0.15,
    "nudge": 0.10,
    "feedback_up": 0.20,
    "reinforce": 0.25,
}


def stability_with_boost(current_stability: float, access_count: int, interaction: str = "retrieve") -> float:
    """Update stability with both spacing-effect growth and interaction boost."""
    boosted = current_stability + INTERACTION_BOOST.get(interaction, 0.15)
    grown = boosted * (1.0 + _ALPHA * math.log(1 + access_count))
    return max(grown, _STABILITY_FLOOR)


# ---------------------------------------------------------------------------
# Deterministic Conflict Resolver (Engraphis-inspired)
# ---------------------------------------------------------------------------
import re as _re
from typing import Optional as _Optional


def tokenize(text: str) -> set[str]:
    """Tokenize text into a set of lowercase alphanumeric tokens (2+ chars)."""
    return {t for t in _re.findall(r"[a-zA-Z0-9]{2,}", text.lower())}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / max(len(union), 1)


# Thresholds (Engraphis defaults, adapted for shorter agent memories)
RELATED_SIM_FLOOR = 0.15
DUP_TOKEN_JACCARD = 0.80
SUBJECT_TOKEN_JACCARD = 0.35
PARAPHRASE_EMBED_SIM = 0.88


class ResolutionOp:
    ADD = "add"
    NOOP = "noop"
    INVALIDATE = "invalidate"


def resolve(
    candidate_text: str,
    neighbors: list[tuple[float, dict]],
) -> tuple[str, int | None, str]:
    """Deterministic ADD / NOOP / INVALIDATE decision against nearest neighbors.

    Args:
        candidate_text: new memory content
        neighbors: list of (embedding_similarity, memory_dict) tuples, scored & scoped

    Returns:
        (op, target_id, reason) where op is ADD/NOOP/INVALIDATE
    """
    cand_tokens = tokenize(candidate_text)
    best: tuple[float, dict, float] | None = None
    best_sim: tuple[float, dict] | None = None

    for sim, mem in neighbors:
        if sim < RELATED_SIM_FLOOR:
            continue
        mem_text = f"{mem.get('content', '')}"
        overlap = jaccard(cand_tokens, tokenize(mem_text))
        if best is None or overlap > best[0]:
            best = (overlap, mem, sim)
        if best_sim is None or sim > best_sim[0]:
            best_sim = (sim, mem)

    if best is None:
        return (ResolutionOp.ADD, None, "no related memory in scope")

    overlap, mem, sim = best
    mid = mem.get("id")

    if overlap >= DUP_TOKEN_JACCARD:
        return (ResolutionOp.NOOP, mid,
                f"near-duplicate (Jaccard={overlap:.2f})")

    if overlap >= SUBJECT_TOKEN_JACCARD:
        return (ResolutionOp.INVALIDATE, mid,
                f"supersedes #{mid} (Jaccard={overlap:.2f}, cos={sim:.2f})")

    if best_sim is not None and best_sim[0] >= PARAPHRASE_EMBED_SIM:
        psim, prec = best_sim
        return (ResolutionOp.INVALIDATE, prec.get("id"),
                f"paraphrase supersedes #{prec.get('id')} (cos={psim:.2f})")

    return (ResolutionOp.ADD, None,
            f"related but distinct (best Jaccard={overlap:.2f})")
