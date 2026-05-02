import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from app.main import app


CHROME_ORIGIN = "chrome-extension://abcdefghijklmnop"


class TestAdminOriginChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_status_requires_origin(self):
        r = self.client.get("/admin/status")
        self.assertEqual(r.status_code, 403)

    def test_status_with_chrome_origin_ok(self):
        r = self.client.get("/admin/status", headers={"Origin": CHROME_ORIGIN})
        self.assertEqual(r.status_code, 200)
        body = r.json()
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
