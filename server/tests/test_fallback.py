import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from app.services import fallback  # noqa: E402


class TestFallbackState(unittest.TestCase):
    def setUp(self):
        fallback._state["enabled"] = True
        fallback._state["api_key"] = None
        fallback._state["model"] = "qwen/qwen3-coder:free"
        fallback._STICKY_UNTIL = None
        fallback._LAST_EVENT.update({"at": None, "reason": None, "model": None, "ok": None, "error": None})

    def test_disabled_when_toggled_off(self):
        fallback.set_enabled(False)
        self.assertFalse(fallback.is_enabled())
        self.assertFalse(fallback.is_available())

    def test_enabled_but_no_key_is_unavailable(self):
        fallback.set_enabled(True)
        fallback.set_api_key(None)
        self.assertTrue(fallback.is_enabled())
        self.assertFalse(fallback.has_api_key())
        self.assertFalse(fallback.is_available())

    def test_enabled_with_key_is_available(self):
        fallback.set_enabled(True)
        fallback.set_api_key("sk-or-v1-abcdef1234567890abcdef1234567890")
        self.assertTrue(fallback.is_available())

    def test_set_api_key_empty_clears(self):
        fallback.set_api_key("sk-or-v1-x")
        fallback.set_api_key("")
        self.assertIsNone(fallback._state["api_key"])

    def test_set_api_key_strips_whitespace(self):
        fallback.set_api_key("  sk-or-v1-padded  ")
        self.assertEqual(fallback._state["api_key"], "sk-or-v1-padded")

    def test_set_model_empty_raises(self):
        with self.assertRaises(ValueError):
            fallback.set_model("")

    def test_set_model_updates(self):
        fallback.set_model("z-ai/glm-4.5-air:free")
        self.assertEqual(fallback.get_model(), "z-ai/glm-4.5-air:free")

    def test_public_state_masks_key(self):
        fallback.set_api_key("sk-or-v1-abcdef1234567890abcdef1234567890")
        s = fallback.get_public_state()
        self.assertTrue(s["has_api_key"])
        self.assertNotIn("sk-or-v1-abcdef1234567890", str(s))
        self.assertTrue(s["api_key_masked"].startswith("sk-or-v"))
        self.assertTrue(s["api_key_masked"].endswith("7890"))
        self.assertNotIn("api_key", s)

    def test_public_state_no_key_no_mask(self):
        fallback.set_api_key(None)
        s = fallback.get_public_state()
        self.assertFalse(s["has_api_key"])
        self.assertIsNone(s["api_key_masked"])

    def test_sticky_active_after_record(self):
        fallback._record(reason="quota", ok=True, model="x", arm_sticky=True)
        if fallback.STICKY_HOURS > 0:
            self.assertTrue(fallback.is_sticky_active())
            self.assertGreater(fallback.sticky_until(), time.time())

    def test_sticky_reset(self):
        fallback._record(reason="quota", ok=True, model="x", arm_sticky=True)
        fallback.reset_sticky()
        self.assertFalse(fallback.is_sticky_active())
        self.assertIsNone(fallback.sticky_until())

    def test_sticky_not_armed_on_failure(self):
        fallback._record(reason="quota", ok=False, model="x", error="boom", arm_sticky=True)
        self.assertFalse(fallback.is_sticky_active())

    def test_sticky_not_armed_when_disabled(self):
        fallback._record(reason="explicit", ok=True, model="x", arm_sticky=False)
        self.assertFalse(fallback.is_sticky_active())

    def test_last_event_records_failure_reason(self):
        fallback._record(reason="auth", ok=False, model="m", error="401 unauthorized", arm_sticky=True)
        ev = fallback.get_last_fallback_event()
        self.assertEqual(ev["reason"], "auth")
        self.assertFalse(ev["ok"])
        self.assertIn("401", ev["error"])


class TestCallOpenRouterRefuses(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fallback._state["enabled"] = True
        fallback._state["api_key"] = None

    async def test_disabled_raises_before_http(self):
        fallback.set_enabled(False)
        with self.assertRaises(fallback.FallbackDisabledError):
            await fallback.call_openrouter_fallback([], None, reason="test")

    async def test_no_key_raises_before_http(self):
        fallback.set_enabled(True)
        fallback.set_api_key(None)
        with self.assertRaises(fallback.FallbackDisabledError) as ctx:
            await fallback.call_openrouter_fallback([], None, reason="test")
        self.assertIn("API key", str(ctx.exception))


class TestEnvResolvers(unittest.TestCase):
    """env > config.conf > fallback chain in gemini_client."""

    def setUp(self):
        for k in (
            "GEMINI_COOKIE_1PSID",
            "GEMINI_COOKIE_1PSIDTS",
            "GEMINI_BRIDGE_ACCOUNT_INDEX",
            "GEMINI_BRIDGE_GEM_ID",
        ):
            os.environ.pop(k, None)

    def test_account_index_env_overrides_config(self):
        from app.services.gemini_client import _resolve_account_index
        os.environ["GEMINI_BRIDGE_ACCOUNT_INDEX"] = "3"
        self.assertEqual(_resolve_account_index(), 3)

    def test_account_index_default_zero(self):
        from app.services.gemini_client import _resolve_account_index
        self.assertEqual(_resolve_account_index(), 0)

    def test_account_index_invalid_falls_to_zero(self):
        from app.services.gemini_client import _resolve_account_index
        os.environ["GEMINI_BRIDGE_ACCOUNT_INDEX"] = "garbage"
        self.assertEqual(_resolve_account_index(), 0)

    def test_gem_id_env_wins(self):
        from app.services.gemini_client import _resolve_initial_gem_id
        os.environ["GEMINI_BRIDGE_GEM_ID"] = "gem-from-env"
        self.assertEqual(_resolve_initial_gem_id(), "gem-from-env")

    def test_gem_id_unset_returns_none(self):
        from app.services.gemini_client import _resolve_initial_gem_id
        self.assertIsNone(_resolve_initial_gem_id())

    def test_cookie_env_wins_over_config(self):
        from app.services.gemini_client import _resolve_cookies
        os.environ["GEMINI_COOKIE_1PSID"] = "psid-env"
        os.environ["GEMINI_COOKIE_1PSIDTS"] = "psidts-env"
        psid, psidts = _resolve_cookies()
        self.assertEqual(psid, "psid-env")
        self.assertEqual(psidts, "psidts-env")


if __name__ == "__main__":
    unittest.main()
