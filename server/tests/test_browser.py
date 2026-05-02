"""Tests for the local-browser cookie fallback. Mocks `browser_cookie3` at the
boundary; CONFIG['Browser'] is patched per case to avoid touching the real one."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from app.utils.browser import _LOADERS, get_cookie_from_browser


def _cookie(name: str, value: str, domain: str = ".google.com"):
    c = MagicMock()
    c.name = name
    c.value = value
    c.domain = domain
    return c


class TestBrowserDispatch(unittest.TestCase):
    def test_supported_browsers(self):
        # Firefox-family + every Chromium fork browser_cookie3 exposes that's
        # plausibly used as a daily driver. Safari was dropped (macOS-only,
        # required Windows-DPAPI-style decryption that we no longer ship).
        self.assertEqual(
            set(_LOADERS.keys()),
            {"firefox", "librewolf", "chrome", "chromium", "brave", "edge",
             "opera", "opera_gx", "vivaldi"},
        )

    def test_unsupported_service_returns_none(self):
        self.assertIsNone(get_cookie_from_browser("openai"))

    def test_unsupported_browser_returns_none(self):
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "safari"}}):
            self.assertIsNone(get_cookie_from_browser("gemini"))

    def test_extracts_pair_from_jar(self):
        jar = [
            _cookie("__Secure-1PSID", "psid-val"),
            _cookie("__Secure-1PSIDTS", "psidts-val"),
            _cookie("OTHER", "ignored"),
        ]
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch.dict(_LOADERS, {"firefox": MagicMock(return_value=jar)}):
            self.assertEqual(get_cookie_from_browser("gemini"), ("psid-val", "psidts-val"))

    def test_chromium_fork_routes_to_its_loader(self):
        jar = [
            _cookie("__Secure-1PSID", "p"),
            _cookie("__Secure-1PSIDTS", "pts"),
        ]
        brave_loader = MagicMock(return_value=jar)
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "brave"}}), \
             patch.dict(_LOADERS, {"brave": brave_loader}):
            self.assertEqual(get_cookie_from_browser("gemini"), ("p", "pts"))
            brave_loader.assert_called_once()

    def test_ignores_non_google_cookies(self):
        jar = [
            _cookie("__Secure-1PSID", "wrong", ".other.com"),
            _cookie("__Secure-1PSIDTS", "wrong", ".other.com"),
        ]
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch.dict(_LOADERS, {"firefox": MagicMock(return_value=jar)}):
            self.assertIsNone(get_cookie_from_browser("gemini"))

    def test_missing_one_cookie_returns_none(self):
        jar = [_cookie("__Secure-1PSID", "psid-only")]
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch.dict(_LOADERS, {"firefox": MagicMock(return_value=jar)}):
            self.assertIsNone(get_cookie_from_browser("gemini"))

    def test_loader_exception_returns_none(self):
        # browser_cookie3 raises (e.g. SQLite locked, profile not found).
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch.dict(_LOADERS, {"firefox": MagicMock(side_effect=RuntimeError("locked"))}):
            self.assertIsNone(get_cookie_from_browser("gemini"))


if __name__ == "__main__":
    unittest.main()
