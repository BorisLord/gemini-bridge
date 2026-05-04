import os
import unittest


class TestEnvResolvers(unittest.TestCase):
    """env > config.conf > fallback chain in gemini_client."""

    def setUp(self):
        for k in (
            "GEMINI_COOKIE_1PSID",
            "GEMINI_COOKIE_1PSIDTS",
            "GEMINI_BRIDGE_ACCOUNT_INDEX",
            "GEMINI_BRIDGE_GEM_ID",
            "GEMINI_BRIDGE_SELECTED_ACCOUNT_ID",
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

    def test_selected_account_id_env_wins(self):
        from app.services.gemini_client import _resolve_selected_account_id
        os.environ["GEMINI_BRIDGE_SELECTED_ACCOUNT_ID"] = "chrome:2"
        # Env wins regardless of what's persisted in config.conf — verified by
        # leaving CONFIG untouched (the real one may contain a different value).
        self.assertEqual(_resolve_selected_account_id(), "chrome:2")

    def test_selected_account_id_unset_falls_back_to_config(self):
        # Patch CONFIG so the test doesn't depend on what's persisted on this
        # dev machine's config.conf (the real bridge may have pinned a value).
        from unittest.mock import patch
        with patch("app.services.gemini_client.CONFIG", {"Cookies": {}}):
            from app.services.gemini_client import _resolve_selected_account_id
            self.assertIsNone(_resolve_selected_account_id())


if __name__ == "__main__":
    unittest.main()
