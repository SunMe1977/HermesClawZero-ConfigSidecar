"""
Integration test — HermesClawZero full-stack smoke test.

Starts containers, waits for health, captures & searches a memory,
then cleans up. Run with:

    python -m unittest tests.test_integration -v

Requires Docker on the host. Skips gracefully if not available.
"""

import os
import json
import time
import unittest
import urllib.request
import urllib.error
import subprocess
import sys

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8010")
COMPOSE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _docker_available():
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


def _wait_health(url: str, timeout: int = 120, interval: int = 3) -> bool:
    for _ in range(timeout // interval):
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _request(method: str, path: str, data: dict = None, headers: dict = None) -> dict:
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            if raw:
                return json.loads(raw)
            return {}
    except urllib.error.HTTPError as e:
        try:
            return {"error": e.read().decode(), "status": e.code}
        except Exception:
            return {"error": str(e), "status": e.code}


class IntegrationSmokeTest(unittest.TestCase):
    """Full-stack smoke test: compose up → health → capture → search → verify."""

    @classmethod
    def setUpClass(cls):
        if not _docker_available():
            raise unittest.SkipTest("Docker not available — skipping integration tests")

        # Start containers if not already running
        cls.containers_started = False
        if not _wait_health(f"{BASE_URL}/healthz", timeout=15):
            print("\n[INTEGRATION] Starting containers...")
            subprocess.run(
                ["docker", "compose", "up", "-d", "--build"],
                cwd=COMPOSE_DIR,
                capture_output=True,
                timeout=300,
            )
            cls.containers_started = True

        healthy = _wait_health(f"{BASE_URL}/healthz", timeout=120)
        if not healthy:
            # Print logs for debugging
            subprocess.run(
                ["docker", "compose", "logs", "api1", "--tail=30"],
                cwd=COMPOSE_DIR,
            )
            raise RuntimeError("API did not become healthy within 120s")

    @classmethod
    def tearDownClass(cls):
        if cls.containers_started:
            print("\n[INTEGRATION] Cleaning up containers...")
            subprocess.run(
                ["docker", "compose", "down", "--remove-orphans"],
                cwd=COMPOSE_DIR,
                capture_output=True,
                timeout=60,
            )

    def test_01_health_endpoint(self):
        """GET /healthz returns 200 with status=ok."""
        resp = _request("GET", "/healthz")
        self.assertIn("status", resp)
        self.assertEqual(resp["status"], "ok")

    def test_02_version_endpoint(self):
        """GET /version returns version info."""
        resp = _request("GET", "/version")
        self.assertIn("version", resp)
        self.assertIn("git", resp)

    def test_03_capture_memory(self):
        """POST /capture saves a memory and returns page_id."""
        resp = _request("POST", "/capture", {
            "text": "Integration test memory — coffee preferences",
            "scope_id": "integration_test",
            "memory_type": "preference",
        })
        self.assertIn("page_id", resp, f"capture failed: {resp}")
        self.captured_id = resp["page_id"]

    def test_04_search_memory(self):
        """GET /search finds the captured memory by text."""
        self._capture_if_needed()
        resp = _request("GET", f"/search?q=coffee+preferences&n=5")
        self.assertIn("results", resp, f"search failed: {resp}")
        results = resp["results"]
        self.assertGreater(len(results), 0, "No search results found")
        found = any("coffee" in r.get("content", "").lower() for r in results)
        self.assertTrue(found, f"Captured memory not found in search results: {results[:2]}")
        self.captured_id = results[0].get("id")

    def test_05_dashboard_loads(self):
        """GET /dashboard returns 200 with HTML content."""
        url = f"{BASE_URL}/dashboard"
        try:
            resp = urllib.request.urlopen(url, timeout=15)
            html = resp.read().decode()
            self.assertIn("Memory Health", html, "Dashboard missing 'Memory Health' section")
            self.assertIn("Memory Galaxy", html, "Dashboard missing 'Memory Galaxy' toggle")
        except urllib.error.HTTPError as e:
            self.fail(f"Dashboard returned {e.code}: {e.read().decode()[:500]}")

    def test_06_memory_timeline(self):
        """GET /timeline returns captured memory."""
        resp = _request("GET", "/timeline?scope_id=integration_test")
        self.assertIn("results", resp, f"timeline failed: {resp}")

    def _capture_if_needed(self):
        if not getattr(self, "captured_id", None):
            self.test_03_capture_memory()


if __name__ == "__main__":
    unittest.main()
