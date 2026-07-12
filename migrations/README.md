# Migrations

## 001 – Import memories from Hermes state.db

**Problem:** After upgrading from v0.1.0 to v2.0.0 (Knowledge Graph release), the
old `memories` database schema was replaced by the new `pages` schema without a
migration path. Existing memories were no longer accessible.

**Solution:** This migration imports capture-worthy messages from Hermes' SQLite
`state.db` into the Sidecar's PostgreSQL `pages` table.

### How to run

```bash
# Inside the API container:
python3 migrations/import_from_hermes_db.py \
    --hermes-db /hermes_state/state.db \
    --dry-run  # Preview only

# Real import:
python3 migrations/import_from_hermes_db.py \
    --hermes-db /hermes_state/state.db
```

On the host via docker exec:
```bash
docker exec hc-sidecar-api-1 sh -c 'DB_PASSWORD=*** python3 /app/repo/migrations/import_from_hermes_db.py --hermes-db /hermes_state/state.db'
```

The `content_hash` column is automatically added to the `pages` table for
future deduplication. Re-running the migration is safe — existing hashes are
skipped.

### What gets imported
- All user and assistant messages from Hermes sessions (>10 chars)
- With source `'messages'` (vs `'capture'` for API captures)
- Content is deduplicated by SHA-256 hash
