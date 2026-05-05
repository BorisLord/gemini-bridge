"""Per-account Gem selection.

Each `Account` carries its own `selected_gem_id` (RAM-only). `/runtime/gem`
writes to the account named by `account_id` in the body — there is no
default account fallback, the field is mandatory.

Auto-detect was dropped (Google's LIST_GEMS RPC is unreliable); user pastes
URL or bare ID directly."""
import unittest

from _helpers import install_registry, seeded_registry
from app.main import app
from app.services.bootstrap import parse_gem_id
from litestar.testing import TestClient

CHROME_ORIGIN = "chrome-extension://abcdefghijklmnop"


class TestParseGemId(unittest.TestCase):
    def test_bare_id_kept_as_is(self):
        self.assertEqual(parse_gem_id("0eb07ff2fcd3"), "0eb07ff2fcd3")

    def test_full_url_u0_extracts_id(self):
        self.assertEqual(
            parse_gem_id("https://gemini.google.com/u/0/gem/eb0eb9162487"),
            "eb0eb9162487",
        )

    def test_full_url_u1_extracts_id(self):
        self.assertEqual(
            parse_gem_id("https://gemini.google.com/u/1/gem/0eb07ff2fcd3"),
            "0eb07ff2fcd3",
        )

    def test_url_with_trailing_query_keeps_id_only(self):
        self.assertEqual(
            parse_gem_id("https://gemini.google.com/u/0/gem/abc-123_xyz?foo=bar"),
            "abc-123_xyz",
        )

    def test_empty_returns_none(self):
        self.assertIsNone(parse_gem_id(""))

    def test_none_returns_none(self):
        self.assertIsNone(parse_gem_id(None))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(parse_gem_id("   "))


class TestSelectGemEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_post_with_account_id_writes_to_that_account(self):
        reg = seeded_registry()
        with install_registry(reg):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "abc-123", "account_id": "firefox:0"},
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["selected_id"], "abc-123")
        self.assertEqual(body["account_id"], "firefox:0")
        self.assertEqual(reg.get("firefox:0").selected_gem_id, "abc-123")

    def test_post_full_url_extracts_id(self):
        with install_registry(seeded_registry()):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={
                    "gem_id": "https://gemini.google.com/u/1/gem/0eb07ff2fcd3",
                    "account_id": "firefox:0",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["selected_id"], "0eb07ff2fcd3")

    def test_post_empty_clears(self):
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

    def test_post_scopes_to_named_account_only(self):
        reg = seeded_registry(account_count=2)
        with install_registry(reg):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "scoped-gem", "account_id": "firefox:1"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["account_id"], "firefox:1")
        self.assertEqual(reg.get("firefox:1").selected_gem_id, "scoped-gem")
        # Sibling account untouched.
        self.assertIsNone(reg.get("firefox:0").selected_gem_id)

    def test_post_unknown_account_id_returns_404(self):
        with install_registry(seeded_registry()):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "x", "account_id": "nope:99"},
            )
        self.assertEqual(r.status_code, 404)

    def test_post_missing_account_id_returns_422(self):
        # No fallback to a default — Pydantic rejects the body.
        with install_registry(seeded_registry()):
            r = self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "x"},
            )
        self.assertEqual(r.status_code, 422)

    def test_status_reflects_per_account_selection(self):
        reg = seeded_registry(account_count=2)
        with install_registry(reg):
            self.client.post(
                "/runtime/gem",
                headers={"Origin": CHROME_ORIGIN},
                json={"gem_id": "https://gemini.google.com/u/0/gem/eb0eb9162487",
                      "account_id": "firefox:0"},
            )
            r = self.client.get("/runtime/status", headers={"Origin": CHROME_ORIGIN})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        accounts = {a["id"]: a for a in body["accounts"]}
        self.assertEqual(accounts["firefox:0"]["selected_gem_id"], "eb0eb9162487")
        self.assertIsNone(accounts["firefox:1"]["selected_gem_id"])


class TestGemPropagatesToChatCompletions(unittest.TestCase):
    """Selected Gem ID on the resolved account must reach `generate_content()`."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_no_gem_passes_none(self):
        reg = seeded_registry()
        with install_registry(reg):
            r = self.client.post("/v1/chat/completions", json={
                "model": "gemini-3-flash@firefox:0",
                "messages": [{"role": "user", "content": "hi"}],
            })
        self.assertEqual(r.status_code, 200)
        kwargs = reg.get("firefox:0").client.generate_content.call_args.kwargs
        self.assertIsNone(kwargs.get("gem"))

    def test_selected_gem_is_forwarded(self):
        reg = seeded_registry()
        reg.get("firefox:0").selected_gem_id = "my-gem-xyz"
        with install_registry(reg):
            r = self.client.post("/v1/chat/completions", json={
                "model": "gemini-3-flash@firefox:0",
                "messages": [{"role": "user", "content": "hi"}],
            })
        self.assertEqual(r.status_code, 200)
        kwargs = reg.get("firefox:0").client.generate_content.call_args.kwargs
        self.assertEqual(kwargs.get("gem"), "my-gem-xyz")

    def test_gem_scoped_to_routed_account(self):
        # Multi-account routing: a request scoped to firefox:1 must use that
        # account's Gem, not any other's.
        reg = seeded_registry(account_count=2)
        reg.get("firefox:0").selected_gem_id = "wrong-gem"
        reg.get("firefox:1").selected_gem_id = "right-gem"
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash@firefox:1",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        kwargs = reg.get("firefox:1").client.generate_content.call_args.kwargs
        self.assertEqual(kwargs.get("gem"), "right-gem")
        reg.get("firefox:0").client.generate_content.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
