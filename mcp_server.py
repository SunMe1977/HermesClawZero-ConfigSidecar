"""
MCP Server: 15 tools exposing all HermesClawZero capabilities to any MCP client.
"""
import sys, json, os, requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("HermesClawZero-Memory")
BASE = os.getenv("MCP_BASE_URL", "http://localhost:8010")
KEY = os.getenv("API_KEY", "change_me_in_env")
_AUTH = None  # lazy

def _headers():
    return {"X-API-Key": KEY, "Content-Type": "application/json"}

def _get_json(path, **params):
    r = requests.get(f"{BASE}{path}", headers=_headers(), params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def _post_json(path, data=None, **params):
    r = requests.post(f"{BASE}{path}", headers=_headers(), params=params,
                      json=data or {}, timeout=30)
    r.raise_for_status()
    return r.json()

# ── CORE MEMORY ──

@mcp.tool()
def search_memory(query: str, limit: int = 5):
    """Search your long-term memory with hybrid vector+lexical scoring."""
    return _get_json("/search", query=query, limit=limit)

@mcp.tool()
def capture_memory(text: str):
    """Capture a new piece of information into long-term memory. Only meaningful facts are stored."""
    return _post_json("/capture", {"text": text})

@mcp.tool()
def ask_question(question: str, scope_id: str = None):
    """Ask a natural language question — searches vector + graph, synthesizes answer via LLM."""
    return _get_json("/ask", q=question, scope_id=scope_id or "")

@mcp.tool()
def memory_feedback(page_id: int, helpful: bool = True):
    """Rate a memory as helpful or not helpful to improve future retrieval."""
    return _get_json(f"/feedback/{page_id}", helpful="true" if helpful else "false")

# ── KNOWLEDGE GRAPH ──

@mcp.tool()
def graph_entities(limit: int = 20):
    """List top entities (people, projects, tools) in the knowledge graph."""
    return _get_json("/graph/entities", limit=limit)

@mcp.tool()
def graph_search(entity_name: str):
    """Search for a specific entity in the knowledge graph."""
    return _get_json("/graph/search", q=entity_name)

@mcp.tool()
def graph_traverse(entity_name: str, depth: int = 1):
    """Traverse relationships from an entity to find connected memories."""
    return _get_json("/graph/traverse", entity=entity_name, depth=depth)

@mcp.tool()
def graph_rag_search(query: str):
    """GraphRAG: search memories via entity graph traversal + reranking."""
    return _get_json("/graph/rag", q=query)

# ── EPISODIC MEMORY ──

@mcp.tool()
def episodic_timeline(scope_id: str = None, project: str = None, limit: int = 50, days_back: int = None):
    """Get episodic memory timeline (events, milestones, decisions, incidents)."""
    return _get_json("/episodic/timeline", scope_id=scope_id, project=project, limit=limit, days_back=days_back)

@mcp.tool()
def record_episode(title: str, description: str = "", episode_type: str = "event", project: str = None):
    """Manually record an episodic memory (event, milestone, decision, incident)."""
    return _post_json("/episodic/record", {"title": title, "description": description,
                     "episode_type": episode_type, "project": project})

# ── MEMORY MANAGEMENT ──

@mcp.tool()
def memory_update(page_id: int, content: str, memory_type: str = None):
    """Edit a stored memory's content and/or type."""
    data = {"content": content}
    if memory_type: data["memory_type"] = memory_type
    return _post_json(f"/memory/update/{page_id}", data)

@mcp.tool()
def memory_merge(source_ids: list):
    """Merge multiple memories into one, combining their content."""
    return _post_json("/memory/merge", {"source_ids": source_ids})

@mcp.tool()
def memory_nudge(limit: int = 5, scope_id: str = None):
    """Get a digest of the most important memories (like a morning brief)."""
    return _get_json("/nudge", limit=limit, scope_id=scope_id)

# ── OPTIMIZER / MAINTENANCE ──

@mcp.tool()
def run_dedup(dry_run: bool = True):
    """Auto-merge duplicate or semantically similar memories."""
    return _post_json("/optimizer/dedup", dry_run="true" if dry_run else "false")

@mcp.tool()
def run_tier_assignment():
    """Recalculate memory tiers (hot/warm/cold) based on importance and recency."""
    return _post_json("/optimizer/tiers")

@mcp.tool()
def run_reflection():
    """Analyze all memories for contradictions and generate scope summaries (uses LLM)."""
    return _post_json("/optimizer/reflect")

@mcp.tool()
def run_optimizer():
    """Run the full optimizer cycle: decay, archive, and consolidate memories."""
    return _post_json("/optimizer/run")

# ── UTILITY ──

@mcp.tool()
def dashboard_stats():
    """Get live dashboard statistics: total memories, tier counts, pending reviews."""
    d = _get_json("/dashboard", limit=1, health_limit=1)
    return {
        "total_memories": d.get("total_items", 0),
        "tiers": d.get("tier_stats", {}),
        "version": d.get("version_info", {}),
    }

if __name__ == "__main__":
    mcp.run()
