"""
Episodic Memory: timeline-based storage of events and project progress.
Inspired by Letta (MemGPT)'s archival memory and Graphiti's temporal graphs.

Stores events as structured entries with timestamps, participants, and project context.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger("hermesclaw.episodic")


def ensure_episodic_schema(conn):
    """Create episodic memory tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
              id SERIAL PRIMARY KEY,
              title TEXT NOT NULL,
              description TEXT,
              episode_type TEXT NOT NULL DEFAULT 'event',
              scope_id TEXT,
              chat_id TEXT DEFAULT 'global',
              participants TEXT[] DEFAULT '{}',
              project TEXT,
              importance REAL DEFAULT 0.5,
              started_at TIMESTAMP DEFAULT NOW(),
              ended_at TIMESTAMP,
              metadata JSONB DEFAULT '{}',
              parent_episode_id INT,
              created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_episodes_scope ON episodes(scope_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(episode_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_episodes_started ON episodes(started_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project)")


def record_episode(conn, title: str, description: str = "",
                   episode_type: str = "event",
                   scope_id: str | None = None,
                   chat_id: str = "global",
                   participants: list[str] | None = None,
                   project: str | None = None,
                   importance: float = 0.5,
                   started_at=None,
                   metadata: dict | None = None) -> int:
    """Record an episodic memory (event, milestone, conversation, etc.)."""
    from hermesclaw.db import connect_db
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO episodes (title, description, episode_type, scope_id, chat_id,
                                     participants, project, importance, started_at, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (title[:200], (description or "")[:2000], episode_type[:50],
             scope_id, chat_id,
             participants or [], project[:200] if project else None,
             importance, started_at or datetime.now(timezone.utc),
             "{}" if not metadata else str(metadata)),
        )
        return cur.fetchone()[0]


def get_timeline(conn, scope_id: str | None = None,
                 project: str | None = None,
                 limit: int = 50,
                 days_back: int | None = None) -> list[dict]:
    """Get episodic timeline, ordered by started_at DESC."""
    conditions = ["1=1"]
    params = []
    if scope_id:
        conditions.append("scope_id = %s")
        params.append(scope_id)
    if project:
        conditions.append("project = %s")
        params.append(project)
    if days_back:
        conditions.append("started_at >= NOW() - %s::interval")
        params.append(f"{days_back} days")

    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, title, description, episode_type, scope_id, chat_id,
                       participants, project, importance, started_at, ended_at, created_at
                FROM episodes
                WHERE {' AND '.join(conditions)}
                ORDER BY started_at DESC
                LIMIT %s""",
            (*params, limit),
        )
        return [
            {"id": r[0], "title": r[1], "description": r[2], "type": r[3],
             "scope": r[4], "chat": r[5], "participants": r[6], "project": r[7],
             "importance": r[8], "started_at": str(r[9]), "ended_at": str(r[10]) if r[10] else None,
             "created_at": str(r[11])}
            for r in cur.fetchall()
        ]


def auto_capture_episode(conn, memory_text: str, scope_id: str | None = None):
    """Auto-detect if a captured memory should also be an episodic timeline entry.
    
    Looks for project, milestone, or event keywords.
    """
    lower = memory_text.lower()
    episode_type = None
    project = None

    # Detect project mentions
    import re
    proj_match = re.search(r'(?:project|repo|repository)["\']?(\w[\w-]+)', lower)
    if proj_match:
        project = proj_match.group(1)

    # Detect episode type
    if any(w in lower for w in ["milestone", "released", "deployed", "launched", "version", "v1.", "v2."]):
        episode_type = "milestone"
    elif any(w in lower for w in ["meeting", "call", "discussed", "sync"]):
        episode_type = "meeting"
    elif any(w in lower for w in ["started", "began", "initiated", "new project"]):
        episode_type = "project_start"
    elif any(w in lower for w in ["decision", "decided", "chose", "going with"]):
        episode_type = "decision"
    elif any(w in lower for w in ["error", "bug", "issue", "problem", "failed", "broken"]):
        episode_type = "incident"

    if episode_type:
        record_episode(conn,
                       title=memory_text[:200],
                       description=memory_text,
                       episode_type=episode_type,
                       scope_id=scope_id,
                       project=project)
        logger.info("Auto-captured episodic memory: %s (%s)", memory_text[:80], episode_type)
