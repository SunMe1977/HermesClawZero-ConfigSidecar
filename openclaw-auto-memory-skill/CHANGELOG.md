# Changelog

## 2.6.0 (2026-07-13)

### Added
- **Pre-Rebuild Backup** — `migrations/pre_rebuild_backup.py` exports all pages before `docker compose down`, restores on startup if DB is empty
- **Backup-before-rebuild** — `UPDATE_RESTART_COMMAND` now runs `pre_rebuild_backup.py backup` before rebuild
- **Two-tier recovery** — startup first tries pre-rebuild backup, then falls back to Hermes state.db import
- **Persistent backup volume** — `pg_backups` volume survives container rebuilds

### Fixed
- `pre_rebuild_backup.py` uses `psycopg` (v3) matching the project, not `psycopg2`
- SQL columns match actual `pages` table schema (no phantom columns)
- SKILL.md version restored to 2.5.1

## 2.5.1 (2026-07-13)

### Added
- **Multi-Replica API** — Redis + Caddy load balancer, 2 API instances (api1/api2)
- **PgBouncer custom Dockerfile** — edoburu/pgbouncer with password via build arg, port 5432
- **Materialized Views** — `pages_stats_mv` + `pages_scope_stats_mv` for dashboard (ms instead of COUNT*)
- **PITR Backup** — WAL archiving (wal_level=replica, archive_mode=on, pgwal volume)
- **Async Embedding Queue** — capture returns instantly, batch worker processes in background
- **HNSW Index Tuning** — m=16→32, ef_construction=400, ef_search×2 for 2M vectors
- **k6 Load Testing** — `tests/loadtest.js` (200 concurrent users, health/search/capture)

### Changed
- **Container naming** — `gbrain-*` → `hc-sidecar-*` (consistent with repo name)
- **API port** — internal 8100, Caddy on 8010 external
- **Dashboard** — COUNT(*) queries replaced by materialized views
- **Optimizer** — refreshes MV + auto-tier + dedup every cycle
- **macOS Setup** — Colima as primary Docker fallback

## 2.5.0 (2026-07-13)

## 2.4.0 (2026-07-13)

### Added
- **Data-loss prevention** — pgvector pinned to `pgvector/pgvector:0.8.0-pg17` (nicht `:latest`), verhindert DB Volume Re-Init bei Auto-Updates
- **Auto-Recovery** — bei `pages < 100` wird Hermes state.db automatisch im Hintergrund importiert
- **DB Backup** — `migrations/backup_db.sh`: täglicher pg_dump mit 14 Tagen Retention
- **15 Data-Loss Tests** — statische Analyse: pgvector-Pin, keine DROP TABLE pages, EMBEDDING_DIM portability, Recovery-Block in main.py
- **CI Gated Pipeline** — 3 Jobs (lint→test→docker) ohne `continue-on-error`, Integration-Tests mit Docker Healthcheck
- **Branch Protection** — main erfordert PR + 1 Review + CI-Checks
- **Pre-push Hook** — `.githooks/pre-push` läuft vor jedem Push auf main

### Fixed
- **EMBEDDING_DIM** portabel gemacht (`${EMBEDDING_DIM}` statt hardcodiertem 1536 → funktioniert wieder mit Ollama)
- **PGUSER/PGDATA** gesetzt → verhindert PostgreSQL Re-Init bei Container-Restart
- **ALLOW_EMBEDDING_SCHEMA_RESET** nur auf embeddings-Tabelle (nicht pages)

## 2.3.0 (2026-07-12)

### Added
- **Bi-temporal endpoints** — `/why/{id}` shows supersession chain + version history; `/timeline` shows all memory changes over time
- **Memory Galaxy v3** — search bar, memory type dropdown, confidence filter in the Galaxy overlay with real-time node filtering
- **MCP auto-discovery** — `mcp.json` manifest for Claude Desktop, VS Code, Cursor; 20 MCP tools total
- **GitHub Actions CI** — 3-job pipeline: lint (syntax+flake8+mypy), test (pytest+coverage), docker build
- **Expanded MCP tools** — `why_memory`, `bi_timeline` MCP tools for bi-temporal queries

### Changed
- AGENTS.md updated to v0.2.0 spec (32 lines, compact API table)
- llms.txt updated with all new features
- GitHub topics: +fastapi, +docker, +knowledge-graph, +ollama, +chat-memory, +semantic-search (19 total)

## 2.2.0 (2026-07-12)

### Added
- **DB backup prevention** — `content_hash` column for idempotent re-imports
- **Migration recovery** — `migrations/import_from_hermes_db.py` restores from Hermes state.db
- **content_hash** — SHA-256 per memory, auto-set on capture, index for fast dedup
- **Migration SQL** — `001_migrate_old_memories.sql` with full schema + indexes

### Changed
- Schema: `content_hash TEXT` + `idx_pages_content_hash` created on every API start
- Memory loss on DB reset is recoverable: run migration script, re-imports idempotently

## 2.1.0 (2026-07-12)

### Added
- **Ebbinghaus forgetting-curve decay** — `R=e^(-t/S)`, stability grows per spacing effect
- **Deterministic Conflict Resolver** — ADD/NOOP/INVALIDATE via token Jaccard + embedding cosine
- **Bi-temporal validity** — `valid_to`/`superseded_by`, superseded facts keep history
- **Interaction-aware reinforcement** — capture=1.0, retrieve=0.15, nudge=0.10
- **7-term hybrid score** — vector + lexical + retention + importance + recency + frequency - staleness
- **10K-scale PG tuning** — shared_buffers=1GB, work_mem=64MB, random_page_cost=1.1
- **Dynamic HNSW ef_search** — per query type (hybrid=80, vector=40, high_recall=400)
- **Dashboard keyset pagination** — cursor-based statt OFFSET
- **`/ask` Q&A endpoint** — natural language → vector + GraphRAG → LLM synthesis
- **`/export` endpoint** — JSON/Markdown backup with graph entities

### Changed
- Optimizer: Ebbinghaus decay archive at retention<0.05 statt flat confidence*0.995
- Search: SQL CTE hybrid score (kein Python merge/sort), 7-term weights
- Capture pipeline: Conflict Resolver ersetzt simple find_similar_page

## 1.6.0 (2026-07-11)

### Added
- **Dashboard UI integrations** — Tier badges (Hot/Warm/Cold), memory type filter, days back filter, Knowledge Graph link, Nudge link
- **Interactive buttons** — 👍/👎 feedback on each memory, ✏️ inline editor modal, 🔗 merge with checkboxes
- **Merge, Editor, Feedback, Nudge** — all new backend features now accessible from the Dashboard UI

## 2.0.0 (2026-07-11)

### Added
- **Knowledge Graph** — entities + relationships + entity_mentions tables, rule-based entity extraction, depth-traversal graph queries (`/graph/*` endpoints)
- **Memory Tiers** — Hot/Warm/Standard/Cold auto-assignment based on importance, recency, confidence
- **Memory Versioning** — `memory_versions` table tracks every change with reason
- **Memory Compression** — intelligent multi-line compression (keep first 40%, last 20%, compress middle)
- **Memory Consolidation** — embedding-similarity clustering groups related memories into summarized parents
- **Memory Merge** — `/memory/merge` endpoint merges multiple memories, reparents sources
- **Memory Editor** — `/memory/update/{id}` inline content + type editing
- **Memory Feedback** — `/feedback/{id}?helpful=true/false` adjusts importance + confidence
- **Memory Nudge** — `/nudge` returns top important recent facts (inspired by Hermes Built-In nudges)
- **Temporal Search** — `?days_back=N` filter on `GET /search`
- **Self-wiring Graph** — 15 relation patterns (built/leads/part_of/implements/located_at/communicates/preceded_by + original 6)
- **pgvector HNSW Index** — automatic HNSW index creation (falls back to IVFFlat)
- **Auto-Sync Daemon** — background thread imports new Hermes sessions every 5 minutes

### Changed
- Capture pipeline now extracts entities + relationships + compressed version on every `POST /capture`
- Optimizer loop runs tier assignment + consolidation every cycle
- Dashboard auto-refresh uses JS setTimeout (galaxy stays open)
- README comparison table expanded to 6 systems (Cognee, LangMem, Supermemory, gBrain, Mem0)

## 1.5.0 (2026-07-11)

### Added
- CONTRIBUTING.md with PR review structure, linting, commit conventions, merge strategy
- PR template (.github/PULL_REQUEST_TEMPLATE.md)
- "Contributions Welcome" section in README and SKILL.md

## 1.4.4 (2026-07-11)

### Added
- Document auto-import step in One-Click Install table

## 1.4.3 (2026-07-11)

### Added
- Subtle CTA at end of README and SKILL.md

## 1.4.2 (2026-07-11)

### Added
- Auto-Capture Cron section in SKILL.md (3 cron jobs for auto-install)

## 1.4.1 (2026-07-11)

### Added
- One-Click Install section in SKILL.md

## 1.4.0 (2026-07-11)

### Added
- Memory Galaxy Dashboard — interactive full-screen Canvas visualization

## 1.3.1 (2026-07-11)

_ClawHub auto-publish_

## 1.1.0 (2026-07-11)
- `secrets.yaml` — documented required environment secrets
- `scripts/install.ps1` — install verification script (checks Python, deps, Sidecar)
- `scripts/update.ps1` — update check script (compares with ClawHub)
- `scripts/smoke_test.ps1` — comprehensive integration tests (8 test cases)
- `README.md` — professional documentation with install steps and examples
- `CHANGELOG.md` — version history

### Changed
- `scripts/memory.py` — major optimization:
  - Structured `SidecarError` class with status/detail
  - Timeout handling (15s) with ConnectionError fallback
  - `ValueError` for empty text validation
  - Config resolution extracted to `_resolve_config()` for testability
  - `_show_result()` helper for clean output formatting
  - Logger integration (debug mode)
  - Input bounds clamping (`limit` capped 1–100)
  - Type annotations throughout
- `SKILL.md` — restructured with privacy disclosure and clearer sections

### Fixed
- All search results now return flat content strings (no raw JSON)
- Empty captures properly rejected before API call
- Connection errors handled gracefully instead of stack traces

## 1.0.1 (2026-07-11)

### Added
- `skill-card.md` — ClawHub marketplace card
- Privacy & consent disclosure in `SKILL.md`

### Fixed
- Security scan: privacy disclosure added to address SkillSpector findings

## 1.0.0 (2026-07-11)

### Added
- Initial publish to ClawHub
- `SKILL.md` with auto-capture/search/autosave instructions
- `scripts/memory.py` CLI tool
