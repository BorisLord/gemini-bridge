"""Auto-detect was dropped (Google's LIST_GEMS RPC is unreliable); user pastes
URL or bare ID directly."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from app.main import app
from app.services import gemini_client as gc


CHROME_ORIGIN = "chrome-extension://abcdefghijklmnop"


class TestGemUrlParsing(unittest.TestCase):
    def setUp(self):
        gc._selected_gem_id = None

    def test_bare_id_kept_as_is(self):
        gc.set_selected_gem_id("0eb07ff2fcd3")
        self.assertEqual(gc.get_selected_gem_id(), "0eb07ff2fcd3")

    def test_full_url_u0_extracts_id(self):
        gc.set_selected_gem_id("https://gemini.google.com/u/0/gem/eb0eb9162487")
        self.assertEqual(gc.get_selected_gem_id(), "eb0eb9162487")

    def test_full_url_u1_extracts_id(self):
        gc.set_selected_gem_id("https://gemini.google.com/u/1/gem/0eb07ff2fcd3")
        self.assertEqual(gc.get_selected_gem_id(), "0eb07ff2fcd3")

    def test_url_with_trailing_slash_or_query(self):
        gc.set_selected_gem_id("https://gemini.google.com/u/0/gem/abc-123_xyz?foo=bar")
        self.assertEqual(gc.get_selected_gem_id(), "abc-123_xyz")

    def test_empty_clears(self):
        gc.set_selected_gem_id("something")
        gc.set_selected_gem_id("")
        self.assertIsNone(gc.get_selected_gem_id())

    def test_none_clears(self):
        gc.set_selected_gem_id("something")
        gc.set_selected_gem_id(None)
        self.assertIsNone(gc.get_selected_gem_id())

    def test_whitespace_only_clears(self):
        gc.set_selected_gem_id("something")
        gc.set_selected_gem_id("   ")
        self.assertIsNone(gc.get_selected_gem_id())


class TestSelectGemEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        gc._selected_gem_id = None

    def test_post_bare_id(self):
        r = self.client.post(
            "/admin/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": "abc-123"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["selected_id"], "abc-123")

    def test_post_full_url_extracts_id(self):
        r = self.client.post(
            "/admin/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": "https://gemini.google.com/u/1/gem/0eb07ff2fcd3"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["selected_id"], "0eb07ff2fcd3")

    def test_post_empty_clears(self):
        gc.set_selected_gem_id("xyz")
        r = self.client.post(
            "/admin/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": ""},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["selected_id"])

    def test_status_reflects_selection(self):
        self.client.post(
            "/admin/gem",
            headers={"Origin": CHROME_ORIGIN},
            json={"gem_id": "https://gemini.google.com/u/0/gem/eb0eb9162487"},
        )
        r = self.client.get("/admin/status", headers={"Origin": CHROME_ORIGIN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["gem"]["selected_id"], "eb0eb9162487")


class TestGemPropagatesToChatCompletions(unittest.TestCase):
    """Selected Gem ID must reach generate_content()."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        gc._selected_gem_id = None

    def tearDown(self):
        gc._gemini_client = None
        gc._selected_gem_id = None

    def test_no_gem_passes_none(self):
        fake_response = MagicMock()
        fake_response.text = "ok"
        fake = MagicMock()
        fake.generate_content = AsyncMock(return_value=fake_response)
        gc._gemini_client = fake

        r = self.client.post("/v1/chat/completions", json={
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hi"}],
        })
        self.assertEqual(r.status_code, 200)
        kwargs = fake.generate_content.call_args.kwargs
        self.assertIsNone(kwargs.get("gem"))

    def test_selected_gem_is_forwarded(self):
        fake_response = MagicMock()
        fake_response.text = "ok"
        fake = MagicMock()
        fake.generate_content = AsyncMock(return_value=fake_response)
        gc._gemini_client = fake
        gc.set_selected_gem_id("my-gem-xyz")

        r = self.client.post("/v1/chat/completions", json={
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hi"}],
        })
        self.assertEqual(r.status_code, 200)
        kwargs = fake.generate_content.call_args.kwargs
        self.assertEqual(kwargs.get("gem"), "my-gem-xyz")


class TestGemEnvBoot(unittest.TestCase):
    """GEMINI_BRIDGE_GEM_ID pre-selects a Gem at boot (headless mode)."""

    def setUp(self):
        gc._selected_gem_id = None
        os.environ.pop("GEMINI_BRIDGE_GEM_ID", None)

    def tearDown(self):
        os.environ.pop("GEMINI_BRIDGE_GEM_ID", None)
        gc._selected_gem_id = None

    @patch.object(gc.MyGeminiClient, "init", new_callable=AsyncMock)
    def test_env_gem_id_applied_at_init(self, _mock_init):
        os.environ["GEMINI_COOKIE_1PSID"] = "fake-psid"
        os.environ["GEMINI_COOKIE_1PSIDTS"] = "fake-psidts"
        os.environ["GEMINI_BRIDGE_GEM_ID"] = "boot-gem-from-env"
        try:
            import asyncio
            ok = asyncio.run(gc.init_gemini_client())
            self.assertTrue(ok)
            self.assertEqual(gc.get_selected_gem_id(), "boot-gem-from-env")
        finally:
            for k in ("GEMINI_COOKIE_1PSID", "GEMINI_COOKIE_1PSIDTS"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
