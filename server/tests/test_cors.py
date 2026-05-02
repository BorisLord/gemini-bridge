"""Locks in the CORS regex fix: Litestar's default `allow_origins=["*"]`
short-circuits the regex check via `is_allow_all_origins=True`. The bridge
must explicitly pass `allow_origins=[]` so the regex is the only matcher.
Regression test in case someone removes that line."""
import unittest

from app.main import app


class TestCORSConfig(unittest.TestCase):
    def setUp(self):
        self.cors = app.cors_config

    def test_cors_is_configured(self):
        self.assertIsNotNone(self.cors)

    def test_not_in_allow_all_mode(self):
        # If True, the regex is bypassed and any Origin (incl. credentialed)
        # is echoed back — full inbound CSRF surface.
        self.assertFalse(self.cors.is_allow_all_origins)

    def test_regex_accepts_chrome_extension_and_loopback(self):
        for origin in (
            "chrome-extension://abcdefghijklmnop",
            "http://localhost:6969",
            "http://localhost",
            "http://127.0.0.1",
            "http://127.0.0.1:6969",
        ):
            with self.subTest(origin=origin):
                self.assertTrue(self.cors.is_origin_allowed(origin))

    def test_regex_rejects_everything_else(self):
        for origin in (
            "https://evil.com",
            "http://evil.com",
            "https://localhost",
            "http://localhost.evil.com",
            "moz-extension://abc",
            "null",
            "",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(self.cors.is_origin_allowed(origin))


if __name__ == "__main__":
    unittest.main()
