import unittest

from _helpers import install_registry, seeded_registry
from app.main import app
from app.services.account_registry import AccountRegistry
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
        with install_registry(seeded_registry()):
            r = self.client.get("/runtime/status", headers={"Origin": CHROME_ORIGIN})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Per-account state. No `default` field — concept removed.
        self.assertIn("accounts", body)
        self.assertNotIn("default", body)

    def test_status_with_x_extension_id_ok(self):
        # Chrome MV3 strips Origin on plain GETs to host_permissions URLs;
        # X-Extension-Id is the documented fallback.
        with install_registry(seeded_registry()):
            r = self.client.get(
                "/runtime/status",
                headers={"X-Extension-Id": "abcdefghijklmnop"},
            )
        self.assertEqual(r.status_code, 200)

    def test_status_rejects_non_extension_origin(self):
        # Anything not starting with chrome-extension:// or moz-extension:// must be 403.
        for origin in ("http://evil.local", "https://evil.com", "safari-web-extension://abc", "null"):
            with self.subTest(origin=origin):
                r = self.client.get("/runtime/status", headers={"Origin": origin})
                self.assertEqual(r.status_code, 403)

    def test_firefox_origin_accepted_on_all_guarded_endpoints(self):
        # Cover every endpoint under `extension_only`; status alone wouldn't catch a Guard regression on /auth/*.
        moz = "moz-extension://12345678-1234-1234-1234-123456789abc"
        with install_registry(seeded_registry()):
            cases = [
                ("GET", "/runtime/status", None),
                ("POST", "/runtime/gem", {"gem_id": "x", "account_id": "firefox:0"}),
                ("POST", "/auth/cookies/gemini",
                 {"cookies": {"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}, "account_index": 0}),
            ]
            for method, path, body in cases:
                with self.subTest(method=method, path=path):
                    r = self.client.request(method, path, headers={"Origin": moz}, json=body)
                    self.assertNotEqual(r.status_code, 403, f"{method} {path} rejected with Firefox origin")

    def test_gem_post_requires_origin(self):
        r = self.client.post("/runtime/gem", json={"gem_id": "anything", "account_id": "firefox:0"})
        self.assertEqual(r.status_code, 403)

    def test_gem_post_sets_selection_when_account_id_provided(self):
        reg = seeded_registry()
        with install_registry(reg):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "abc-123", "account_id": "firefox:0"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["selected_id"], "abc-123")
        self.assertEqual(reg.get("firefox:0").selected_gem_id, "abc-123")

    def test_gem_post_clears_with_empty(self):
        reg = seeded_registry()
        reg.get("firefox:0").selected_gem_id = "xyz"
        with install_registry(reg):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "", "account_id": "firefox:0"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["selected_id"])
        self.assertIsNone(reg.get("firefox:0").selected_gem_id)

    def test_gem_post_missing_account_id_returns_422(self):
        # Routing is mandatory everywhere — Pydantic rejects the body before
        # the handler runs.
        with install_registry(seeded_registry()):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "abc"},
            )
        self.assertEqual(r.status_code, 422)

    def test_chat_completions_no_origin_check(self):
        # Public API: must not be origin-gated (expect 400 for missing routing, never 403).
        r = self.client.post("/v1/chat/completions", json={"messages": [], "model": "gemini-3-flash"})
        self.assertNotEqual(r.status_code, 403)

    def test_gem_post_accepts_x_extension_id_only(self):
        # POST endpoints under the Guard must also honor the X-Extension-Id
        # fallback (not just GETs) — same contract for the whole controller.
        with install_registry(seeded_registry()):
            r = self.client.post(
                "/runtime/gem",
                headers={"X-Extension-Id": "abcdefghijklmnop"},
                json={"gem_id": "x-ext-test", "account_id": "firefox:0"},
            )
        self.assertEqual(r.status_code, 200)


class TestAccountIndexValidation(unittest.TestCase):
    """Bounds locked at 0..7 to mirror Chrome's max simultaneous Google profiles
    and `probe_gemini_account`'s scan range. Out-of-range values must 422.

    The endpoint upserts into a registry — we install a fresh one per call so
    test runs don't pollute global state."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _post(self, account_index):
        with install_registry(AccountRegistry()):
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


class TestExtensionPushPopulatesEmail(unittest.TestCase):
    """The extension forwards the Google email it cached from
    `/auth/accounts/gemini` so `/accounts/` shows it for `extension:N` entries
    (the server's own probe would fail on Chrome's device-bound cookies)."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_push_with_email_populates_account(self):
        reg = AccountRegistry()
        with install_registry(reg):
            r = self.client.post(
                "/auth/cookies/gemini",
                headers={"X-Extension-Id": "x"},
                json={
                    "cookies": {"__Secure-1PSID": "p", "__Secure-1PSIDTS": "pts"},
                    "account_index": 1,
                    "email": "alice@example.com",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(reg.get("extension:1").email, "alice@example.com")

    def test_push_without_email_leaves_email_null(self):
        reg = AccountRegistry()
        with install_registry(reg):
            r = self.client.post(
                "/auth/cookies/gemini",
                headers={"X-Extension-Id": "x"},
                json={
                    "cookies": {"__Secure-1PSID": "p", "__Secure-1PSIDTS": "pts"},
                    "account_index": 0,
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(reg.get("extension:0").email)


if __name__ == "__main__":
    unittest.main()
