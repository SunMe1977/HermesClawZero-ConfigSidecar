import unittest
from unittest.mock import patch

import main


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


class ValidateSecurityStartupTests(unittest.TestCase):
    def _set_globals(self, api_key, dashboard_password, session_secret):
        main.API_KEY = api_key
        main.DASHBOARD_PASSWORD = dashboard_password
        main.DASHBOARD_SESSION_SECRET = session_secret

    def test_validate_security_startup_rejects_default_dashboard_password(self):
        self._set_globals(api_key="key-123", dashboard_password="admin", session_secret="very-strong-secret")

        with self.assertRaises(RuntimeError) as cm:
            main.validate_security_startup()

        self.assertIn("DASHBOARD_PASSWORD", str(cm.exception))

    def test_validate_security_startup_rejects_missing_session_secret(self):
        self._set_globals(api_key="key-123", dashboard_password="custom-pass", session_secret="")

        with self.assertRaises(RuntimeError) as cm:
            main.validate_security_startup()

        self.assertIn("DASHBOARD_SESSION_SECRET", str(cm.exception))

    def test_validate_security_startup_rejects_reused_api_key_as_session_secret(self):
        self._set_globals(api_key="same-secret", dashboard_password="custom-pass", session_secret="same-secret")

        with self.assertRaises(RuntimeError) as cm:
            main.validate_security_startup()

        self.assertIn("must not reuse API_KEY", str(cm.exception))

    def test_validate_security_startup_accepts_strong_distinct_values(self):
        self._set_globals(
            api_key="api-key-value",
            dashboard_password="dashboard-pass-!234",
            session_secret="session-secret-very-long-and-random",
        )

        main.validate_security_startup()


class EnsureEmbeddingSchemaTests(unittest.TestCase):
    def test_mismatch_raises_when_destructive_reset_disabled(self):
        # exists=True, current_type='vector(768)' while expected is vector(1536)
        cursor = FakeCursor(fetchone_values=[(True,), ("vector(768)",)])
        conn = FakeConn(cursor)

        with patch("main.connect_db", return_value=conn), patch("main.ALLOW_EMBEDDING_SCHEMA_RESET", False):
            with self.assertRaises(RuntimeError) as cm:
                main.ensure_embeddings_schema(expected_dim=1536)

        self.assertIn("Automatic destructive reset is disabled", str(cm.exception))

    def test_mismatch_resets_when_explicitly_enabled(self):
        cursor = FakeCursor(fetchone_values=[(True,), ("vector(768)",)])
        conn = FakeConn(cursor)

        with patch("main.connect_db", return_value=conn), patch("main.ALLOW_EMBEDDING_SCHEMA_RESET", True):
            main.ensure_embeddings_schema(expected_dim=1536)

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

        score, explain = main.compute_hybrid_score(item)

        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertIn("components", explain)
        self.assertIn("weights", explain)
        self.assertIn("final_score", explain)


if __name__ == "__main__":
    unittest.main()
