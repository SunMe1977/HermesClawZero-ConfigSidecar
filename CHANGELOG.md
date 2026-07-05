# Changelog

All notable changes to this project are documented in this file.

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
- `/transcribe` now requires API key auth and uses safe temporary file handling.
- Dashboard/API auth path handling hardened for `/dashboard` and trailing-slash variants.
- README architecture `<picture>` now includes PNG fallback.

### Security
- Removed hardcoded Postgres password from `Dockerfile.postgres`.
- Added explicit Compose-time validation for required `DB_PASSWORD` and `API_KEY`.

### Fixed
- Rerank crash due to missing `json` import.
- Dashboard delete 422 due to incorrect form parameter binding.
- Multiple dashboard auth UX issues causing repeated prompts or plain unauthorized responses.
