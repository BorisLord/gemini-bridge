"""Tests for the local-browser cookie fallback. Mocks
`gemini_webapi.utils.load_browser_cookies` at the boundary; CONFIG['Browser']
is patched per case to avoid touching the real one."""
import unittest
from unittest.mock import MagicMock, patch

from app.utils.browser import _extract_pair, get_all_cookie_pairs, get_cookie_from_browser


def _ck(name: str, value: str) -> dict:
    """Mirror gemini-webapi's cookie dict shape."""
    return {"name": name, "value": value, "domain": ".google.com", "path": "/", "expires": 0}


class TestBrowserDispatch(unittest.TestCase):
    def test_unsupported_service_returns_none(self):
        self.assertIsNone(get_cookie_from_browser("openai"))

    def test_empty_lib_result_returns_none(self):
        with patch("app.utils.browser.load_browser_cookies", return_value={}):
            self.assertIsNone(get_cookie_from_browser("gemini"))

    def test_extracts_pair_from_preferred_browser(self):
        result = {
            "firefox": [_ck("__Secure-1PSID", "psid-val"), _ck("__Secure-1PSIDTS", "psidts-val"),
                        _ck("OTHER", "ignored")],
        }
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertEqual(get_cookie_from_browser("gemini"), ("psid-val", "psidts-val"))

    def test_calls_lib_with_google_domain_filter(self):
        mock = MagicMock(return_value={})
        with patch("app.utils.browser.load_browser_cookies", mock):
            get_cookie_from_browser("gemini")
        mock.assert_called_once_with(domain_name="google.com")

    def test_preferred_browser_wins_over_others(self):
        """When `[Browser].name` is set and present in the lib result, that
        browser's cookies are used even if other browsers also have valid pairs."""
        result = {
            "chrome": [_ck("__Secure-1PSID", "chrome-psid"), _ck("__Secure-1PSIDTS", "chrome-psidts")],
            "firefox": [_ck("__Secure-1PSID", "ff-psid"), _ck("__Secure-1PSIDTS", "ff-psidts")],
        }
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertEqual(get_cookie_from_browser("gemini"), ("ff-psid", "ff-psidts"))

    def test_falls_back_to_any_browser_when_preferred_incomplete(self):
        """Multi-account / multi-browser robustness: if preferred browser
        only has one of the two cookies (or none), try the others — Google
        sessions can live across multiple browsers."""
        result = {
            "firefox": [_ck("__Secure-1PSID", "ff-psid-only")],  # missing PSIDTS
            "chrome": [_ck("__Secure-1PSID", "chrome-psid"), _ck("__Secure-1PSIDTS", "chrome-psidts")],
        }
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertEqual(get_cookie_from_browser("gemini"), ("chrome-psid", "chrome-psidts"))

    def test_no_preference_picks_any_browser_with_pair(self):
        result = {
            "edge": [_ck("__Secure-1PSID", "edge-psid"), _ck("__Secure-1PSIDTS", "edge-psidts")],
        }
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": ""}}), \
             patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertEqual(get_cookie_from_browser("gemini"), ("edge-psid", "edge-psidts"))

    def test_unknown_preferred_browser_falls_back(self):
        """`[Browser].name = safari` but lib doesn't return safari — should
        still pick another browser with a valid pair instead of returning None."""
        result = {
            "brave": [_ck("__Secure-1PSID", "brave-psid"), _ck("__Secure-1PSIDTS", "brave-psidts")],
        }
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "safari"}}), \
             patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertEqual(get_cookie_from_browser("gemini"), ("brave-psid", "brave-psidts"))

    def test_missing_one_cookie_in_only_browser_returns_none(self):
        result = {"firefox": [_ck("__Secure-1PSID", "psid-only")]}
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertIsNone(get_cookie_from_browser("gemini"))

    def test_empty_string_cookie_value_treated_as_missing(self):
        result = {"firefox": [_ck("__Secure-1PSID", "  "), _ck("__Secure-1PSIDTS", "psidts-val")]}
        with patch("app.utils.browser.CONFIG", {"Browser": {"name": "firefox"}}), \
             patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertIsNone(get_cookie_from_browser("gemini"))


class TestExtractPair(unittest.TestCase):
    """Direct coverage for the cookie-list → (psid, psidts) extraction.
    Anything callers feed in (lib output, test fixtures) must hit these
    branches consistently — both `get_cookie_from_browser` and
    `get_all_cookie_pairs` rely on the same edge handling."""

    def test_returns_pair_when_both_present(self):
        cookies = [_ck("__Secure-1PSID", "p"), _ck("__Secure-1PSIDTS", "pts"), _ck("OTHER", "x")]
        self.assertEqual(_extract_pair(cookies), ("p", "pts"))

    def test_missing_psid_returns_none(self):
        self.assertIsNone(_extract_pair([_ck("__Secure-1PSIDTS", "pts")]))

    def test_missing_psidts_returns_none(self):
        self.assertIsNone(_extract_pair([_ck("__Secure-1PSID", "p")]))

    def test_empty_list_returns_none(self):
        self.assertIsNone(_extract_pair([]))

    def test_whitespace_only_value_treated_as_missing(self):
        cookies = [_ck("__Secure-1PSID", "  "), _ck("__Secure-1PSIDTS", "pts")]
        self.assertIsNone(_extract_pair(cookies))


class TestGetAllCookiePairs(unittest.TestCase):
    """Multi-browser harvest: every browser with a complete (1PSID, 1PSIDTS)
    pair is surfaced. Foundation for the cross-browser account discovery flow."""

    def test_unsupported_service_returns_empty(self):
        self.assertEqual(get_all_cookie_pairs("openai"), {})

    def test_empty_lib_result_returns_empty(self):
        with patch("app.utils.browser.load_browser_cookies", return_value={}):
            self.assertEqual(get_all_cookie_pairs("gemini"), {})

    def test_returns_pair_per_browser_with_complete_cookies(self):
        result = {
            "firefox": [_ck("__Secure-1PSID", "ff-psid"), _ck("__Secure-1PSIDTS", "ff-psidts")],
            "chrome": [_ck("__Secure-1PSID", "ch-psid"), _ck("__Secure-1PSIDTS", "ch-psidts")],
            "brave": [_ck("__Secure-1PSID", "br-psid"), _ck("__Secure-1PSIDTS", "br-psidts")],
        }
        with patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertEqual(get_all_cookie_pairs("gemini"), {
                "firefox": ("ff-psid", "ff-psidts"),
                "chrome": ("ch-psid", "ch-psidts"),
                "brave": ("br-psid", "br-psidts"),
            })

    def test_drops_browsers_with_incomplete_cookies(self):
        """Browser logged in halfway (e.g. just 1PSID, no PSIDTS) is unusable."""
        result = {
            "firefox": [_ck("__Secure-1PSID", "ff-psid"), _ck("__Secure-1PSIDTS", "ff-psidts")],
            "chrome": [_ck("__Secure-1PSID", "ch-psid")],  # missing PSIDTS
            "edge": [],
        }
        with patch("app.utils.browser.load_browser_cookies", return_value=result):
            self.assertEqual(get_all_cookie_pairs("gemini"), {
                "firefox": ("ff-psid", "ff-psidts"),
            })

    def test_calls_lib_with_google_domain_filter(self):
        mock = MagicMock(return_value={})
        with patch("app.utils.browser.load_browser_cookies", mock):
            get_all_cookie_pairs("gemini")
        mock.assert_called_once_with(domain_name="google.com")


if __name__ == "__main__":
    unittest.main()
