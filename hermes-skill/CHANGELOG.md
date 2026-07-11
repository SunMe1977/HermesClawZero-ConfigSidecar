# Changelog

## 1.1.0 (2026-07-11)

### Added
- `permissions.yaml` — declared minimum permissions with justifications
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
