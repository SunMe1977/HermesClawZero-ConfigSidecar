"""Module-level smoke tests for refactored hermesclaw package."""

import os
import unittest
from unittest.mock import patch


# Set minimum env vars before importing modules that read configuration at import time.
os.environ["API_KEY"] = "test-key-123"
os.environ["DB_HOST"] = "localhost"
os.environ["DB_PASSWORD"] = "test"
os.environ["DASHBOARD_PASSWORD"] = "custom-pass-strong"
os.environ["DASHBOARD_SESSION_SECRET"] = "test-session-secret-very-random"


class ModuleImportSmokeTests(unittest.TestCase):
    def test_all_modules_import(self):
        import hermesclaw
        from hermesclaw.config import (
            API_KEY,
            DASHBOARD_PASSWORD,
            DASHBOARD_SESSION_SECRET,
            env_bool,
            SCOPE_ALIASES,
            _load_scope_aliases,
        )
        from hermesclaw.models import CaptureRequest, BatchCaptureRequest, ArchiveSelectionRequest
        from hermesclaw.db import embedding_to_pgvector_literal
        from hermesclaw.auth import validate_security_startup, _build_dashboard_session_token
        from hermesclaw.scoring import (
            clamp,
            classify_memory_type,
            estimate_sentiment,
            score_memory,
            compute_hybrid_score,
            format_scope_label,
            build_scope_filter,
            normalize_scope_id,
            normalize_chat_id,
        )
        from hermesclaw.embeddings import resolve_embedding_provider, provider_runtime_info
        from hermesclaw.memory import find_similar_page, rerank
        from hermesclaw.optimizer import run_decay_and_archive_once
        from hermesclaw.update import get_version_info
        from hermesclaw.routes import router

        self.assertIsNotNone(hermesclaw)
        self.assertTrue(callable(env_bool))
        self.assertIsNotNone(router)
        self.assertEqual(API_KEY, "test-key-123")
        self.assertEqual(DASHBOARD_PASSWORD, "custom-pass-strong")
        self.assertEqual(DASHBOARD_SESSION_SECRET, "test-session-secret-very-random")
        self.assertIsInstance(SCOPE_ALIASES, dict)
        self.assertTrue(callable(_load_scope_aliases))
        self.assertIsNotNone(CaptureRequest)
        self.assertIsNotNone(BatchCaptureRequest)
        self.assertIsNotNone(ArchiveSelectionRequest)
        self.assertTrue(callable(embedding_to_pgvector_literal))
        self.assertTrue(callable(validate_security_startup))
        self.assertTrue(callable(_build_dashboard_session_token))
        self.assertTrue(callable(clamp))
        self.assertTrue(callable(classify_memory_type))
        self.assertTrue(callable(estimate_sentiment))
        self.assertTrue(callable(score_memory))
        self.assertTrue(callable(compute_hybrid_score))
        self.assertTrue(callable(format_scope_label))
        self.assertTrue(callable(build_scope_filter))
        self.assertTrue(callable(normalize_scope_id))
        self.assertTrue(callable(normalize_chat_id))
        self.assertTrue(callable(resolve_embedding_provider))
        self.assertTrue(callable(provider_runtime_info))
        self.assertTrue(callable(find_similar_page))
        self.assertTrue(callable(rerank))
        self.assertTrue(callable(run_decay_and_archive_once))
        self.assertTrue(callable(get_version_info))


class ModuleBehaviorSmokeTests(unittest.TestCase):
    def test_scoring_and_sentiment(self):
        from hermesclaw.scoring import score_memory, estimate_sentiment

        pref = score_memory("I prefer coffee over tea")
        project = score_memory("Project deadline is next Friday")
        convo = score_memory("Just having a normal conversation")

        self.assertEqual(pref["memory_type"], "preference")
        self.assertEqual(project["memory_type"], "project")
        self.assertEqual(convo["memory_type"], "conversation")
        self.assertGreater(pref["score"], 0)

        self.assertGreater(estimate_sentiment("This is great and wonderful!"), 0)
        self.assertLess(estimate_sentiment("This is a bad error and a problem"), 0)
        self.assertLess(abs(estimate_sentiment("The sky is blue")), 0.5)

    def test_hybrid_score_shape(self):
        from hermesclaw.scoring import compute_hybrid_score

        item = {
            "vector_distance": 0.2,
            "lexical_rank": 0.8,
            "importance": 0.9,
            "confidence": 0.7,
            "age_days": 2.0,
            "frequency": 5,
        }
        score, explain = compute_hybrid_score(item)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 1)
        self.assertIn("components", explain)
        self.assertIn("weights", explain)
        self.assertIn("reasons", explain)

    def test_scope_helpers(self):
        from hermesclaw.scoring import (
            normalize_scope_id,
            normalize_chat_id,
            build_scope_filter,
            clamp,
            format_scope_label,
        )

        self.assertEqual(normalize_scope_id("  test  "), "test")
        self.assertIsNone(normalize_scope_id(""))
        self.assertIsNone(normalize_scope_id(None))
        self.assertEqual(normalize_chat_id(None), "global")
        self.assertEqual(normalize_chat_id(""), "global")
        self.assertEqual(normalize_chat_id("my-chat"), "my-chat")

        clause_all, params_all = build_scope_filter("all", "scope_id")
        self.assertEqual(clause_all, "")
        self.assertEqual(params_all, [])

        clause_scope, params_scope = build_scope_filter("my_scope", "scope_id")
        self.assertIn("AND", clause_scope)
        self.assertEqual(params_scope, ["my_scope"])

        clause_unscoped, params_unscoped = build_scope_filter("__unscoped__", "scope_id")
        self.assertIn("IS NULL", clause_unscoped)
        self.assertEqual(params_unscoped, [])

        self.assertEqual(clamp(1.5, 0, 1), 1.0)
        self.assertEqual(clamp(-0.5, 0, 1), 0.0)
        self.assertEqual(clamp(0.5, 0, 1), 0.5)

        self.assertIn("Telegram", format_scope_label("telegram:12345"))
        self.assertIn("Openclaw", format_scope_label("openclaw:user_abc"))

    def test_env_bool_security_and_token(self):
        from hermesclaw.config import env_bool
        from hermesclaw.auth import validate_security_startup, _build_dashboard_session_token

        with patch.dict(os.environ, {"TEST_FLAG": "1"}, clear=False):
            self.assertTrue(env_bool("TEST_FLAG"))
        with patch.dict(os.environ, {"TEST_FLAG": "false"}, clear=False):
            self.assertFalse(env_bool("TEST_FLAG"))

        self.assertFalse(env_bool("NONEXISTENT_FLAG"))
        validate_security_startup()

        token = _build_dashboard_session_token("admin")
        self.assertIsInstance(token, str)
        self.assertTrue(token)


if __name__ == "__main__":
    unittest.main()
