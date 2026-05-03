import unittest
from unittest.mock import AsyncMock, patch

from app.main import app
from litestar.testing import TestClient

CHROME_ORIGIN = "chrome-extension://abcdefghijklmnop"


class TestRuntimeOriginChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_status_requires_origin(self):
        r = self.client.get("/runtime/status")
        self.assertEqual(r.status_code, 403)

    def test_status_with_chrome_origin_ok(self):
        r = self.client.get("/runtime/status", headers={"Origin": CHROME_ORIGIN})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("gem", body)

    def test_status_with_x_extension_id_ok(self):
        # Chrome MV3 strips Origin on plain GETs to host_permissions URLs;
        # X-Extension-Id is the documented fallback.
        r = self.client.get(
            "/runtime/status",
            headers={"X-Extension-Id": "abcdefghijklmnop"},
        )
        self.assertEqual(r.status_code, 200)

    def test_status_rejects_non_extension_origin(self):
        # Anything not starting with chrome-extension:// must be 403, regardless of scheme.
        for origin in ("http://evil.local", "https://evil.com", "moz-extension://abc", "null"):
            with self.subTest(origin=origin):
                r = self.client.get("/runtime/status", headers={"Origin": origin})
                self.assertEqual(r.status_code, 403)

    def test_gem_post_requires_origin(self):
        r = self.client.post("/runtime/gem", json={"gem_id": "anything"})
        self.assertEqual(r.status_code, 403)

    def test_gem_post_sets_selection(self):
        from app.services.gemini_client import get_selected_gem_id
        r = self.client.post(
            "/runtime/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": "abc-123"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["selected_id"], "abc-123")
        self.assertEqual(get_selected_gem_id(), "abc-123")

    def test_gem_post_clears_with_empty(self):
        from app.services.gemini_client import get_selected_gem_id, set_selected_gem_id
        set_selected_gem_id("xyz")
        r = self.client.post(
            "/runtime/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": ""},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(get_selected_gem_id())

    def test_chat_completions_no_origin_check(self):
        # Public API: must not be origin-gated (expect 503/400, never 403).
        r = self.client.post("/v1/chat/completions", json={"messages": [], "model": "gemini-3-flash"})
        self.assertNotEqual(r.status_code, 403)

    def test_gem_post_accepts_x_extension_id_only(self):
        # POST endpoints under the Guard must also honor the X-Extension-Id
        # fallback (not just GETs) — same contract for the whole controller.
        r = self.client.post(
            "/runtime/gem",
            headers={"X-Extension-Id": "abcdefghijklmnop"},
            json={"gem_id": "x-ext-test"},
        )
        self.assertEqual(r.status_code, 200)


class TestAccountIndexValidation(unittest.TestCase):
    """Bounds locked at 0..7 to mirror Chrome's max simultaneous Google profiles
    and `probe_gemini_account`'s scan range. Out-of-range values must 422.

    `refresh_gemini_client` is mocked so validation tests don't make real
    network calls or persist cookies to the real `server/config.conf`."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _post(self, account_index):
        # Mock the refresh path: any successful validation reaches it; we don't
        # care about the auth outcome here, only the Pydantic boundary.
        with patch("app.endpoints.auth.refresh_gemini_client",
                   new=AsyncMock(return_value="refreshed")):
            return self.client.post(
                "/auth/cookies/gemini",
                headers={"X-Extension-Id": "x"},
                json={
                    "cookies": {"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"},
                    "account_index": account_index,
                },
            )

    def test_lower_bound_zero_passes_validation(self):
        self.assertEqual(self._post(0).status_code, 200)

    def test_upper_bound_seven_passes_validation(self):
        self.assertEqual(self._post(7).status_code, 200)

    def test_negative_rejected(self):
        self.assertEqual(self._post(-1).status_code, 422)

    def test_above_seven_rejected(self):
        self.assertEqual(self._post(8).status_code, 422)


if __name__ == "__main__":
    unittest.main()
