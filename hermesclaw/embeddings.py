"""Embedding generation: Ollama, OpenAI, OpenRouter, Gemini."""

import time
import logging
import requests
import ollama
from fastapi import HTTPException
from hermesclaw.config import (
    OLLAMA_HOST, AI_PROVIDER, EMBEDDING_PROVIDER,
    OPENAI_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY,
    OPENAI_EMBED_MODEL, OPENROUTER_EMBED_MODEL, GEMINI_EMBED_MODEL,
    EMBEDDING_DIM, OPENROUTER_MAX_RETRIES, OPENROUTER_RETRY_BASE_SECONDS,
    OPENROUTER_DEGRADED_MESSAGE,
)

logger = logging.getLogger("hermesclaw.embeddings")

client = ollama.Client(host=OLLAMA_HOST)


def resolve_embedding_provider() -> str:
    valid = {"auto", "ollama", "openai", "openrouter", "gemini"}
    if EMBEDDING_PROVIDER not in valid:
        raise HTTPException(
            status_code=500,
            detail="Invalid EMBEDDING_PROVIDER. Use one of: auto, ollama, openai, openrouter, gemini",
        )
    if EMBEDDING_PROVIDER != "auto":
        return EMBEDDING_PROVIDER

    if AI_PROVIDER in valid:
        return AI_PROVIDER

    if AI_PROVIDER == "anthropic":
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

    logger.warning("[OPENROUTER] embedding retries exhausted: %s", last_error or "unknown error")
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
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": OPENAI_EMBED_MODEL, "input": text},
            timeout=45,
        )
        if response.status_code >= 400:
            _raise_embedding_upstream_error("OpenAI", response)
        return response.json()["data"][0]["embedding"]

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
            json={"model": model_name, "content": {"parts": [{"text": text}]}},
            timeout=45,
        )
        if response.status_code >= 400:
            _raise_embedding_upstream_error("Gemini", response)
        return response.json()["embedding"]["values"]

    raise HTTPException(
        status_code=500,
        detail="Unsupported embedding provider. Use one of: ollama, openai, openrouter, gemini",
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
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": OPENAI_EMBED_MODEL, "input": texts},
            timeout=90,
        )
        if response.status_code >= 400:
            _raise_embedding_upstream_error("OpenAI", response)
        data_sorted = sorted(response.json().get("data", []), key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data_sorted]

    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is required when AI_PROVIDER=openrouter")
        response_payload = _openrouter_embeddings_request(
            payload={"model": OPENROUTER_EMBED_MODEL, "input": texts},
            timeout_seconds=90,
        )
        data_sorted = sorted(response_payload.get("data", []), key=lambda d: d.get("index", 0))
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
            "anthropic": bool(OPENAI_API_KEY),
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
