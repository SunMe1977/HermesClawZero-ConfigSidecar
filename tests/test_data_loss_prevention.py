"""Tests to prevent data loss and verify auto-recovery mechanisms."""
import os
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.normpath(os.path.join(TESTS_DIR, ".."))


def _read(path):
    with open(os.path.join(REPO_DIR, path)) as f:
        return f.read()


# ── 1. Dockerfile.postgres: pgvector image must be pinned ──

class DockerfilePgvectorPinnedTest(unittest.TestCase):
    """Data-loss prevention: pgvector :latest → auto-rebuild kills DB volume."""

    def test_pgvector_image_is_pinned(self):
        content = _read("Dockerfile.postgres")
        self.assertIn("pgvector/pgvector:", content)
        self.assertNotIn("pgvector:latest", content,
            ":latest causes silent PG major version upgrades → data loss!")
        for line in content.splitlines():
            if "FROM pgvector/pgvector:" in line and "latest" not in line:
                return
        self.fail("Must pin pgvector to a specific version, not :latest")


# ── 2. Auto-recovery: pages < 100 triggers Hermes state.db import ──

class AutoRecoveryTest(unittest.TestCase):
    """Verify main.py has the recovery block and it runs async."""

    def test_recovery_block_exists(self):
        content = _read("main.py")
        self.assertIn("import_from_hermes_db.py", content,
            "main.py must trigger Hermes state.db import on low page count")
        self.assertIn("daemon=True", content,
            "Recovery must run in background thread (daemon)")
        self.assertIn("--minimal", content,
            "Recovery must use --minimal for fast startup")

    def test_recovery_runs_from_startup_event(self):
        content = _read("main.py")
        # Find the recovery block inside startup_event
        self.assertIn("page_count < 100", content,
            "Recovery must check if pages < 100")
        self.assertIn("threading.Thread", content,
            "Recovery must use threading.Thread for background execution")

    def test_no_synchronous_blocking_import_in_startup(self):
        """The subprocess.run for import must be inside the thread target, not in startup flow."""
        content = _read("main.py")
        startup = content.split("def startup_event")[1] if "def startup_event" in content else ""
        # The 'def _run_recovery' should be defined inside startup_event
        self.assertIn("def _run_recovery", startup,
            "Recovery function must be defined inside startup_event")
        # The subprocess.run must be inside the def, not before it
        recovery_def = startup.split("def _run_recovery")[1] if "def _run_recovery" in startup else ""
        self.assertIn("capture_output=True", recovery_def,
            "subprocess.run must be inside _run_recovery, not blocking startup")


# ── 3. Schema safety: ALLOW_EMBEDDING_SCHEMA_RESET only drops embeddings ──

class SchemaSafetyTest(unittest.TestCase):
    """ensure_embeddings_schema must never DROP the pages table."""

    def test_no_drop_pages_in_db_py(self):
        content = _read(os.path.join("hermesclaw", "db.py"))
        drop_lines = [l for l in content.splitlines()
                      if "DROP TABLE" in l.upper() or "DROP TABLE" in l]
        for line in drop_lines:
            self.assertNotIn("pages", line.lower(),
                f"DROP TABLE must never target pages: {line}")
        self.assertTrue(len(drop_lines) > 0,
            "Expected DROP TABLE statement(s) for embeddings")

    def test_drop_only_mentions_embeddings(self):
        content = _read(os.path.join("hermesclaw", "db.py"))
        for line in content.splitlines():
            if "DROP TABLE" in line.upper():
                self.assertIn("embeddings", line.lower(),
                    f"Only embeddings table may be dropped: {line}")

    def test_no_delete_from_pages_in_db_py(self):
        content = _read(os.path.join("hermesclaw", "db.py"))
        for line in content.splitlines():
            if "DELETE FROM" in line.upper() or "delete from" in line.lower():
                self.assertNotIn("pages", line.lower(),
                    f"DELETE FROM must never target pages table in db.py: {line}")

    def test_allow_reset_env_var_present_in_compose(self):
        content = _read("docker-compose.yml")
        self.assertIn("ALLOW_EMBEDDING_SCHEMA_RESET", content,
            "ALLOW_EMBEDDING_SCHEMA_RESET must be set in docker-compose.yml")


# ── 4. EMBEDDING_DIM portability ──

class EmbeddingDimPortabilityTest(unittest.TestCase):
    """EMBEDDING_DIM must not be hardcoded to 1536 in docker-compose.yml."""

    def test_not_hardcoded_1536(self):
        content = _read("docker-compose.yml")
        for line in content.splitlines():
            if "EMBEDDING_DIM:" in line:
                self.assertNotIn("1536", line,
                    "EMBEDDING_DIM hardcoded to 1536 breaks Ollama (768 dim)!")
                self.assertIn("${EMBEDDING_DIM}", line,
                    "Must use ${EMBEDDING_DIM} for .env / auto-detect fallback")

    def test_old_ankane_image_not_present(self):
        content = _read("docker-compose.yml")
        self.assertNotIn("ankane/pgvector", content,
            "ankane/pgvector Image (with :latest risk) removed from compose")


# ── 5. Backup script ──

class BackupScriptTest(unittest.TestCase):
    """migrations/backup_db.sh must be valid shell and use safe commands."""

    def test_exists_and_has_shebang(self):
        content = _read(os.path.join("migrations", "backup_db.sh"))
        self.assertTrue(content.startswith("#!/"), "Must have shebang")

    def test_uses_pg_dump_not_destructive(self):
        content = _read(os.path.join("migrations", "backup_db.sh"))
        self.assertIn("pg_dump", content, "Must use pg_dump")
        self.assertIn("gzip", content, "Should compress")
        self.assertNotIn("DROP TABLE", content.upper(), "No DROP")
        self.assertNotIn("rm -rf", content, "No rm -rf")


# ── 6. Docker compose has PGUSER/PGDATA/backup mount ──

class DockerComposePgSafetyTest(unittest.TestCase):
    """docker-compose.yml must have PG persistence safeguards."""

    def test_pguser_set(self):
        content = _read("docker-compose.yml")
        self.assertIn("PGUSER: postgres", content,
            "PGUSER prevents re-init on container restart")

    def test_pgdata_set(self):
        content = _read("docker-compose.yml")
        self.assertIn("PGDATA: /var/lib/postgresql/data", content,
            "PGDATA must match existing cluster path")

    def test_backup_mount_present(self):
        content = _read("docker-compose.yml")
        self.assertIn("backup_db.sh", content,
            "Backup script mount in docker-compose.yml")


if __name__ == "__main__":
    unittest.main()
