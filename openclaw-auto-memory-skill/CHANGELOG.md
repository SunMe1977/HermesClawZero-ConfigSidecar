# Changelog

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
