"""
Playwright dashboard test — verifies the dashboard renders correctly.

Requires: pip install playwright && playwright install chromium
Run:    python tests/test_dashboard.py
CI:     pytest tests/test_dashboard.py --browser chromium
"""

import os
import sys
import base64
import subprocess
import time

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8010/dashboard")
# The dashboard is protected by HTTP Basic auth. Playwright must send the
# credentials in the browser context, otherwise the page renders the login
# challenge (or a 401 body) and the UI assertions fail.
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    print("SKIP: playwright not installed")
    sys.exit(0)

def test_dashboard_loads():
    """Dashboard page loads without error and shows key UI elements."""
    if not DASHBOARD_PASSWORD:
        print("SKIP: DASHBOARD_PASSWORD not set")
        sys.exit(0)

    token = base64.b64encode(
        f"{DASHBOARD_USER}:{DASHBOARD_PASSWORD}".encode()
    ).decode()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # http_credentials only kicks in after the server sends a 401
        # challenge; sending the Authorization header explicitly is reliable
        # for every request the page makes.
        context = browser.new_context(extra_http_headers={"Authorization": f"Basic {token}"})
        page = context.new_page()
        page.set_default_timeout(30000)

        # Navigate with auth if needed
        page.goto(DASHBOARD_URL)

        # Should not see error text
        error_texts = ["Dashboard Error", "Internal Server Error", "502 Bad Gateway", "503 Service Unavailable"]
        for t in error_texts:
            count = page.get_by_text(t, exact=False).count()
            assert count == 0, f"Page shows error: {t}"

        # Should see key UI elements
        assert page.get_by_text("Memory Dashboard", exact=False).count() > 0, "No dashboard title"
        assert page.get_by_text("Memory Galaxy", exact=False).count() > 0, "No Memory Galaxy toggle"
        assert page.get_by_text("Memory Health", exact=False).count() > 0, "No Health section"

        # Check stats cards are present
        assert page.get_by_text("Total Memories", exact=False).count() > 0, "No Total Memories stat"

        # Should see the memory table or empty state
        has_memories = page.get_by_text("No memories found", exact=False).count() == 0
        if has_memories:
            assert page.locator("table.memory-table").count() > 0 or page.locator(".memory-table").count() > 0, "No memory table"

        # Verify the page is not dumping raw CSS as text. The original check
        # counted locator(":root") elements, which always matches the <html>
        # element, so it could never be 0 and only the byte-length fallback
        # was ever evaluated. Check for visible CSS text instead.
        body_text = page.inner_text("body")
        assert "--" not in body_text[:2000], "Raw CSS variables displayed as text"
        assert "{" not in body_text[:2000], "Raw CSS rules displayed as text"

        browser.close()
        print("PASS: Dashboard renders correctly")


def test_dashboard_health_section():
    """Health section shows expected controls."""
    if not DASHBOARD_PASSWORD:
        print("SKIP: DASHBOARD_PASSWORD not set")
        sys.exit(0)

    token = base64.b64encode(
        f"{DASHBOARD_USER}:{DASHBOARD_PASSWORD}".encode()
    ).decode()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(extra_http_headers={"Authorization": f"Basic {token}"})
        page = context.new_page()
        page.goto(DASHBOARD_URL)

        # Health controls should exist
        assert page.get_by_text("Stale days", exact=False).count() > 0, "No stale days filter"
        assert page.get_by_text("Run Optimizer", exact=False).count() > 0, "No Run Optimizer button"

        browser.close()
        print("PASS: Dashboard health section renders")


def test_dashboard_version_info():
    """Page includes version info."""
    if not DASHBOARD_PASSWORD:
        print("SKIP: DASHBOARD_PASSWORD not set")
        sys.exit(0)

    token = base64.b64encode(
        f"{DASHBOARD_USER}:{DASHBOARD_PASSWORD}".encode()
    ).decode()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(extra_http_headers={"Authorization": f"Basic {token}"})
        page = context.new_page()
        page.goto(DASHBOARD_URL)

        content = page.content()
        assert "0.1.0" in content or "version" in content.lower(), "No version info found"

        browser.close()
        print("PASS: Dashboard version info present")


if __name__ == "__main__":
    test_dashboard_loads()
    test_dashboard_health_section()
    test_dashboard_version_info()
    print("\nAll dashboard tests passed.")
