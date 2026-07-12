"""
Export/Backup: dump all memories as JSON or Markdown.
"""
import json, logging
from datetime import datetime
from hermesclaw.db import connect_db

logger = logging.getLogger("hermesclaw.export")


def export_memories(format: str = "json", scope_id: str | None = None,
                    include_archived: bool = False, include_graph: bool = False) -> dict | str:
    """Export all memories. Returns dict for JSON, str for markdown."""
    with connect_db() as conn:
        with conn.cursor() as cur:
            # Memories
            conditions = ["1=1"]
            params = []
            if scope_id:
                conditions.append("p.scope_id = %s")
                params.append(scope_id)
            if not include_archived:
                conditions.append("p.is_archived = FALSE")
            cur.execute(
                f"""SELECT p.id, p.content, p.memory_type, p.importance, p.confidence,
                           p.frequency, p.source, p.memory_tier, p.scope_id, p.chat_id,
                           p.created_at, p.updated_at, p.last_used, p.is_archived
                    FROM pages p
                    WHERE {' AND '.join(conditions)}
                    ORDER BY p.id ASC""",
                params,
            )
            memories = []
            for r in cur.fetchall():
                mem = {
                    "id": r[0], "content": r[1], "type": r[2],
                    "importance": r[3], "confidence": r[4], "frequency": r[5],
                    "source": r[6], "tier": r[7], "scope": r[8], "chat": r[9],
                    "created": str(r[10]), "updated": str(r[11]), "last_used": str(r[12]),
                    "archived": r[13],
                }
                # Attach top entities if graph included
                if include_graph:
                    cur2 = conn.cursor()
                    cur2.execute(
                        """SELECT e.name, e.entity_type FROM entities e
                           JOIN entity_mentions em ON em.entity_id = e.id
                           WHERE em.page_id = %s LIMIT 5""",
                        (r[0],),
                    )
                    mem["entities"] = [{"name": e[0], "type": e[1]} for e in cur2.fetchall()]
                memories.append(mem)

            stats = {"total": len(memories), "scopes": len(set(m["scope"] for m in memories))}

            # Episodic timeline
            cur.execute("SELECT id, title, episode_type, project, started_at, importance FROM episodes ORDER BY started_at DESC LIMIT 100")
            episodes = [{"id": e[0], "title": e[1], "type": e[2], "project": e[3],
                        "started": str(e[4]), "importance": e[5]} for e in cur.fetchall()]

    export = {
        "exported_at": datetime.utcnow().isoformat(),
        "version": "1.8.0",
        "stats": stats,
        "memories": memories,
        "episodes": episodes,
    }

    if format == "markdown":
        return _to_markdown(export)
    return export


def _to_markdown(data: dict) -> str:
    lines = [
        f"# HermesClawZero Export",
        f"**Exported at:** {data['exported_at']}  ",
        f"**Version:** {data['version']}  ",
        f"**Total memories:** {data['stats']['total']}  ",
        f"**Scopes:** {data['stats']['scopes']}  ",
        f"**Episodes:** {len(data['episodes'])}  ",
        "",
        "---",
        "",
    ]
    for mem in data["memories"]:
        lines.append(f"## #{mem['id']} — {mem['type']} [{mem['tier']}]")
        lines.append(f"")
        lines.append(mem["content"][:1000])
        lines.append(f"")
        lines.append(f"**Importance:** {mem['importance']} | **Confidence:** {mem['confidence']} | **Frequency:** {mem['frequency']}")
        lines.append(f"**Scope:** {mem['scope']} | **Chat:** {mem['chat']} | **Tier:** {mem['tier']}")
        if mem.get("entities"):
            lines.append(f"**Entities:** {', '.join(e['name'] for e in mem['entities'])}")
        lines.append(f"**Created:** {mem['created']} | **Last used:** {mem['last_used']}")
        lines.append(f"")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)
