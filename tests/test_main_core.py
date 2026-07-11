"""Core unit tests for HermesClawZero module structure."""

import os
import unittest
from unittest.mock import patch

# Set minimum env vars before importing anything
os.environ["API_KEY"] = "test-key-123"
os.environ["DASHBOARD_PASSWORD"] = "strong-pass-!@#"
os.environ["DASHBOARD_SESSION_SECRET"] = "test-session-secret-very-strong"

from hermesclaw.config import env_bool
from hermesclaw.scoring import compute_hybrid_score, score_memory, clamp
from hermesclaw.db import embedding_to_pgvector_literal


class ValidateSecurityStartupTests(unittest.TestCase):
    def _set_globals(self, api_key, dashboard_password, session_secret):
        import hermesclaw.config as cfg
        cfg.API_KEY = api_key
        cfg.DASHBOARD_PASSWORD = dashboard_password
        cfg.DASHBOARD_SESSION_SECRET = session_secret

    def test_validate_security_startup_rejects_default_dashboard_password(self):
        self._set_globals(api_key="key-123", dashboard_password="admin", session_secret="very-strong-secret")
        from hermesclaw.auth import validate_security_startup
        with self.assertRaises(RuntimeError) as cm:
            validate_security_startup()
        self.assertIn("DASHBOARD_PASSWORD", str(cm.exception))

    def test_validate_security_startup_rejects_missing_session_secret(self):
        self._set_globals(api_key="key-123", dashboard_password="custom-pass", session_secret="")
        from hermesclaw.auth import validate_security_startup
        with self.assertRaises(RuntimeError) as cm:
            validate_security_startup()
        self.assertIn("DASHBOARD_SESSION_SECRET", str(cm.exception))

    def test_validate_security_startup_rejects_reused_api_key_as_session_secret(self):
        self._set_globals(api_key="same-secret", dashboard_password="custom-pass", session_secret="same-secret")
        from hermesclaw.auth import validate_security_startup
        with self.assertRaises(RuntimeError) as cm:
            validate_security_startup()
        self.assertIn("must not reuse API_KEY", str(cm.exception))

    def test_validate_security_startup_accepts_strong_distinct_values(self):
        self._set_globals(
            api_key="api-key-strong",
            dashboard_password="dashboard-pass-!234",
            session_secret="session-secret-very-long-and-random",
        )
        from hermesclaw.auth import validate_security_startup
        validate_security_startup()


class FakeCursor:
    def __init__(self, fetchone_values):
        self._fetchone_values = list(fetchone_values)
        self.executed_sql = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed_sql.append((sql, params))

    def fetchone(self):
        if not self._fetchone_values:
            return None
        return self._fetchone_values.pop(0)


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commit_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_calls += 1


class EnsureEmbeddingSchemaTests(unittest.TestCase):
    def test_mismatch_raises_when_destructive_reset_disabled(self):
        cursor = FakeCursor(fetchone_values=[(True,), ("vector(768)",)])
        conn = FakeConn(cursor)

        with patch("hermesclaw.db.connect_db", return_value=conn), \
             patch("hermesclaw.db.ALLOW_EMBEDDING_SCHEMA_RESET", False):
            from hermesclaw.db import ensure_embeddings_schema
            with self.assertRaises(RuntimeError) as cm:
                ensure_embeddings_schema(expected_dim=1536)
            self.assertIn("Automatic destructive reset is disabled", str(cm.exception))

    def test_mismatch_resets_when_explicitly_enabled(self):
        cursor = FakeCursor(fetchone_values=[(True,), ("vector(768)",)])
        conn = FakeConn(cursor)

        with patch("hermesclaw.db.connect_db", return_value=conn), \
             patch("hermesclaw.db.ALLOW_EMBEDDING_SCHEMA_RESET", True):
            from hermesclaw.db import ensure_embeddings_schema
            ensure_embeddings_schema(expected_dim=1536)

        statements = "\n".join(sql for sql, _ in cursor.executed_sql)
        self.assertIn("DROP TABLE IF EXISTS embeddings", statements)
        self.assertIn("CREATE TABLE embeddings", statements)


class HybridScoreTests(unittest.TestCase):
    def test_compute_hybrid_score_returns_expected_shape_and_range(self):
        item = {
            "vector_distance": 0.2,
            "lexical_rank": 0.8,
            "importance": 0.9,
            "confidence": 0.7,
            "age_days": 2.0,
            "frequency": 5,
        }
        score, explain = compute_hybrid_score(item)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertIn("components", explain)
        self.assertIn("weights", explain)
        self.assertIn("final_score", explain)


class ScoringTests(unittest.TestCase):
    def test_memory_scoring_preference(self):
        result = score_memory("I prefer coffee over tea")
        self.assertEqual(result["memory_type"], "preference")
        self.assertGreater(result["score"], 0)

    def test_memory_scoring_project(self):
        result = score_memory("Project deadline is next Friday")
        self.assertEqual(result["memory_type"], "project")
        self.assertGreater(result["score"], 0)

    def test_memory_scoring_conversation(self):
        result = score_memory("Just having a normal conversation")
        self.assertEqual(result["memory_type"], "conversation")
        self.assertGreater(result["score"], 0)

    def test_clamp(self):
        self.assertEqual(clamp(1.5, 0, 1), 1.0)
        self.assertEqual(clamp(-0.5, 0, 1), 0.0)
        self.assertEqual(clamp(0.5, 0, 1), 0.5)

    def test_embedding_literal(self):
        lit = embedding_to_pgvector_literal([0.1, 0.2, 0.3])
        self.assertEqual(lit, "[0.1,0.2,0.3]")


if __name__ == "__main__":
    unittest.main()
