import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
