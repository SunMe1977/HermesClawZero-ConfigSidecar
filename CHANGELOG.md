# Changelog

All notable changes to this project are documented in this file.

## [0.2.0] - 2026-07-12

### Added
- `/why/{page_id}` bi-temporal endpoint — shows supersession chain, version history, and valid_to range for any memory
- `/timeline` bi-temporal endpoint — aggregated timeline of all memory changes (supersessions, edits, new memories) with scope/day filters
- Memory Galaxy v3 with filter/search — search bar, memory type dropdown, confidence filter in the Galaxy overlay; real-time node filtering
- `mcp.json` — MCP server auto-discovery manifest for Claude Desktop, VS Code, and other MCP clients
- MCP tools `why_memory` and `bi_timeline` exposing new bi-temporal endpoints
- GitHub Actions CI expanded — 3-job pipeline: lint (syntax check all .py files + flake8 + mypy + yaml validate), test (pytest + coverage), docker (build API + Postgres images with caching)

### Changed
- Exempt prefixes in main.py extended with `/why` and `/timeline`
- Version bumped from 0.1.0 to 0.2.0

## [1.0.0] - 2026-07-05

### Added
- Architecture assets in `images/` and README visual improvements.
- Health endpoint: `/healthz` for runtime monitoring.
- Docker build optimization via `.dockerignore`.
- Docker Compose health checks for `db`, `api`, and `ollama`.
- Docker Compose restart policies for all services.
- README FAQ and expanded troubleshooting section.

### Changed
- `setup.sh` now loads `.env` defaults with robust parsing (handles spaces and special characters).
- `setup.ps1` and `setup.sh` now auto-detect updater git remote, branch, and repository path.
- API key auth handling was hardened for protected endpoints.
- Dashboard/API auth path handling hardened for `/dashboard` and trailing-slash variants.
- README architecture `<picture>` now includes PNG fallback.

### Security
- Removed hardcoded Postgres password from `Dockerfile.postgres`.
- Added explicit Compose-time validation for required `DB_PASSWORD` and `API_KEY`.

### Fixed
- Rerank crash due to missing `json` import.
- Dashboard delete 422 due to incorrect form parameter binding.
- Multiple dashboard auth UX issues causing repeated prompts or plain unauthorized responses.
