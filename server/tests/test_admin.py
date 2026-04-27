import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import fallback  # noqa: E402


CHROME_ORIGIN = "chrome-extension://abcdefghijklmnop"


class TestAdminOriginChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        # Reset module state so tests don't bleed.
        fallback._state["enabled"] = True
        fallback._state["api_key"] = None
        fallback._state["model"] = "qwen/qwen3-coder:free"
        fallback._STICKY_UNTIL = None

    def test_status_requires_origin(self):
        r = self.client.get("/admin/status")
        self.assertEqual(r.status_code, 403)

    def test_status_with_chrome_origin_ok(self):
        r = self.client.get("/admin/status", headers={"Origin": CHROME_ORIGIN})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("openrouter", body)
        self.assertIn("last_fallback", body)
        self.assertIn("gem", body)

    def test_status_with_x_extension_id_ok(self):
        # Chrome MV3 strips Origin on plain GETs to host_permissions URLs;
        # X-Extension-Id is the documented fallback.
        r = self.client.get(
            "/admin/status",
            headers={"X-Extension-Id": "abcdefghijklmnop"},
        )
        self.assertEqual(r.status_code, 200)

    def test_status_rejects_http_origin(self):
        r = self.client.get("/admin/status", headers={"Origin": "http://evil.local"})
        self.assertEqual(r.status_code, 403)

    def test_openrouter_post_requires_origin(self):
        r = self.client.post("/admin/openrouter", json={"enabled": False})
        self.assertEqual(r.status_code, 403)

    def test_openrouter_post_toggles_enabled(self):
        r = self.client.post(
            "/admin/openrouter",
            headers={"Origin": CHROME_ORIGIN},
            json={"enabled": False},
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])
        self.assertFalse(fallback.is_enabled())

    def test_openrouter_post_sets_key_and_masks_in_response(self):
        key = "sk-or-v1-abcdef1234567890abcdef1234567890"
        r = self.client.post(
            "/admin/openrouter",
            headers={"Origin": CHROME_ORIGIN},
            json={"api_key": key},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["has_api_key"])
        self.assertNotIn(key, r.text)
        self.assertEqual(fallback._state["api_key"], key)

    def test_openrouter_post_updates_model(self):
        r = self.client.post(
            "/admin/openrouter",
            headers={"Origin": CHROME_ORIGIN},
            json={"model": "z-ai/glm-4.5-air:free"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(fallback.get_model(), "z-ai/glm-4.5-air:free")

    def test_reset_fallback_requires_origin(self):
        r = self.client.post("/admin/reset-fallback")
        self.assertEqual(r.status_code, 403)

    def test_reset_fallback_clears_sticky(self):
        # Force sticky window > 0 regardless of env so the assertion is unconditional.
        original_hours = fallback.STICKY_HOURS
        fallback.STICKY_HOURS = 1.0
        try:
            fallback._record(reason="quota", ok=True, model="x", arm_sticky=True)
            self.assertTrue(fallback.is_sticky_active())
            r = self.client.post("/admin/reset-fallback", headers={"Origin": CHROME_ORIGIN})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(fallback.is_sticky_active())
        finally:
            fallback.STICKY_HOURS = original_hours

    def test_gem_post_requires_origin(self):
        r = self.client.post("/admin/gem", json={"gem_id": "anything"})
        self.assertEqual(r.status_code, 403)

    def test_gem_post_sets_selection(self):
        from app.services.gemini_client import get_selected_gem_id
        r = self.client.post(
            "/admin/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": "abc-123"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["selected_id"], "abc-123")
        self.assertEqual(get_selected_gem_id(), "abc-123")

    def test_gem_post_clears_with_empty(self):
        from app.services.gemini_client import set_selected_gem_id, get_selected_gem_id
        set_selected_gem_id("xyz")
        r = self.client.post(
            "/admin/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": ""},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(get_selected_gem_id())

    def test_chat_completions_no_origin_check(self):
        # Public API: must not be origin-gated (expect 503/400, never 403).
        r = self.client.post("/v1/chat/completions", json={"messages": [], "model": "gemini-3-flash"})
        self.assertNotEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
