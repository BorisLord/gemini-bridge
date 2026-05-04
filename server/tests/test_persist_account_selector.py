"""`persist_selected_account_id` writes `[Cookies].selected_account_id` to
`config.conf` with `chmod 0o600` (the selector reveals which Google account
is bound to this bridge — same leak surface as the cookies themselves)."""
import configparser
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.gemini_client import persist_selected_account_id


class TestPersistAccountSelector(unittest.TestCase):
    def _patched_config(self, cfg_path: Path):
        return patch.multiple(
            "app.services.gemini_client",
            _CONFIG_PATH=cfg_path,
            CONFIG={"Cookies": {}},
        )

    def test_chmods_to_0600(self):
        with TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.conf"
            cfg_path.write_text("[Cookies]\n")
            with self._patched_config(cfg_path):
                persist_selected_account_id("firefox:0")
            mode = cfg_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600, f"expected 0o600, got {oct(mode)}")

    def test_creates_config_when_missing(self):
        # Docker fresh-install: very first POST /accounts/use must survive a restart.
        with TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "nested" / "config.conf"
            self.assertFalse(cfg_path.exists())
            with self._patched_config(cfg_path):
                persist_selected_account_id("firefox:0")
            self.assertTrue(cfg_path.exists())
            cfg = configparser.ConfigParser()
            cfg.read(cfg_path, encoding="utf-8")
            self.assertEqual(cfg["Cookies"]["selected_account_id"], "firefox:0")

    def test_clears_when_none(self):
        with TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.conf"
            cfg_path.write_text("[Cookies]\nselected_account_id = firefox:0\n")
            with self._patched_config(cfg_path):
                persist_selected_account_id(None)
            cfg = configparser.ConfigParser()
            cfg.read(cfg_path, encoding="utf-8")
            self.assertNotIn("selected_account_id", cfg["Cookies"])

    def test_preserves_other_keys(self):
        with TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.conf"
            cfg_path.write_text(
                "[Cookies]\n"
                "gemini_cookie_1psid = keep-me\n"
                "gemini_cookie_1psidts = and-me\n"
            )
            with self._patched_config(cfg_path):
                persist_selected_account_id("chrome:2")
            cfg = configparser.ConfigParser()
            cfg.read(cfg_path, encoding="utf-8")
            self.assertEqual(cfg["Cookies"]["gemini_cookie_1psid"], "keep-me")
            self.assertEqual(cfg["Cookies"]["gemini_cookie_1psidts"], "and-me")
            self.assertEqual(cfg["Cookies"]["selected_account_id"], "chrome:2")


if __name__ == "__main__":
    unittest.main()
