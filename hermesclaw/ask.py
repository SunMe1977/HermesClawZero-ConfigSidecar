"""
/ask endpoint: Natural language question → vector search + GraphRAG → LLM answer.
Combines all retrieval methods into a single synthesized response.
"""
import json, logging, ollama
from hermesclaw.config import OLLAMA_HOST
from hermesclaw.db import connect_db
from hermesclaw.memory import _search_sync
from hermesclaw.graph import graph_rag_search

logger = logging.getLogger("hermesclaw.ask")

_ASK_MODEL = None
def _get_model():
    global _ASK_MODEL
    if _ASK_MODEL:
        return _ASK_MODEL
    
    # Try multiple Ollama instances (Docker stack 11435, host 11434)
    hosts = [OLLAMA_HOST or "http://host.docker.internal:11435", "http://host.docker.internal:11434"]
    tried = set()
    for host in hosts:
        if host in tried:
            continue
        tried.add(host)
        try:
            client = ollama.Client(host=host)
            tags = client.list()
            models = [m.model for m in tags.models if "embed" not in (m.model or "")]
            for pref in ["qwen2.5:7b-instruct", "llama3.1:8b", "llama3.1:latest",
                         "qwen2.5-coder:7b", "gemma4:e2b", "llama2:7b"]:
                for m in models:
                    if m.startswith(pref) or m == pref:
                        _ASK_MODEL = m
                        logger.info("ASK model: %s on %s", _ASK_MODEL, host)
                        return _ASK_MODEL
            if models:
                _ASK_MODEL = models[0]
                return _ASK_MODEL
        except Exception as e:
            logger.warning("ASK model discovery failed on %s: %s", host, e)
            continue
    logger.warning("ASK model fallback to llama3.1:8b (no models found on any host)")
    _ASK_MODEL = "llama3.1:8b"
    return _ASK_MODEL


def ask_question(question: str, scope_id: str | None = None, chat_id: str = "global",
                 llm_generate=None, limit: int = 8) -> dict:
    """Answer a natural language question using vector + graph retrieval + LLM synthesis.
    
    When llm_generate is not provided, auto-discovers Ollama host and model.
    """
    if not question.strip():
        return {"status": "error", "error": "question required"}

    # Phase 1: Vector search
    vector_results = _search_sync(
        query=question, limit=limit, rerank_results=True,
        scope_id=scope_id, chat_id=chat_id, search_type="hybrid"
    )

    # Phase 2: GraphRAG
    graph_results = []
    with connect_db() as conn:
        entities = [w.strip(".,!?") for w in question.split()
                    if len(w.strip(".,!?")) > 3 and w[0].isupper()]
        if entities:
            graph_results = graph_rag_search(conn, entities, query_text=question, limit=limit)

    # Phase 3: Combine + deduplicate
    seen_ids = set()
    combined = []
    for r in vector_results:
        rid = r.get("id") or id(r)
        if rid not in seen_ids:
            seen_ids.add(rid)
            combined.append({
                "id": rid, "content": r.get("content", r.get("text", "")),
                "score": r.get("score", 0.5), "source": "vector",
                "memory_type": r.get("memory_type", "memory")
            })
    for r in graph_results:
        rid = r.get("id", 0)
        if rid not in seen_ids:
            seen_ids.add(rid)
            combined.append({
                "id": rid, "content": r.get("content", ""),
                "score": r.get("score", 0.3) * 0.8, "source": "graph",
                "memory_type": "graph"
            })

    combined.sort(key=lambda x: x["score"], reverse=True)
    top_context = combined[:limit]

    # Phase 4: LLM synthesis
    answer = None
    synthesis_detail = ""

    # Auto-discover Ollama if no generate function provided
    if not llm_generate and top_context:
        model = _get_model()
        for host in ["http://host.docker.internal:11435", "http://host.docker.internal:11434"]:
            try:
                c = ollama.Client(host=host)
                tags = c.list()
                if any(m.model == model for m in tags.models):
                    llm_generate = c.generate
                    break
            except Exception:
                continue

    if llm_generate and top_context:
        context_str = "\n\n".join(
            f"[{r['source']}] (confidence={r['score']:.2f}): {r['content'][:500]}"
            for r in top_context
        )
        prompt = (
            "You are an AI assistant with access to stored memories. "
            "Answer the user's question based ONLY on the context below. "
            "If the context doesn't contain enough information, say so honestly.\n\n"
            f"Memory Context:\n{context_str}\n\n"
            f"Question: {question}\n\n"
            "Provide a concise, helpful answer using the context. "
            "Return ONLY the answer text with key facts cited as [source: type]."
        )
        try:
            resp = llm_generate(model=model, prompt=prompt)
            answer = resp.get("response", "").strip()
        except Exception as ex:
            answer = f"Error generating answer: {ex}"
            synthesis_detail = "llm_failed"
    else:
        answer = "No LLM available or no context found."
        synthesis_detail = "no_context" if not top_context else "no_llm"

    return {
        "status": "ok",
        "question": question,
        "answer": answer,
        "sources": top_context,
        "meta": {
            "vector_results": len(vector_results),
            "graph_results": len(graph_results),
            "combined": len(combined),
            "synthesis": synthesis_detail or "ok",
        }
    }
