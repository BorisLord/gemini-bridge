"""Boot-time wiring of `AccountRegistry`.

The lifespan calls `bootstrap_registry` which:
  1. upserts `env:0` if `GEMINI_COOKIE_*` env / config provides cookies
  2. runs cross-browser discovery and upserts every `<browser>:<idx>` found

There is no "default account" anymore — routing is always explicit per
request, so nothing to pin and nothing to error about. `bootstrap_registry`
must therefore complete cleanly even on an empty machine."""
import unittest
from unittest.mock import AsyncMock, patch

from app.services.account_registry import Account, AccountRegistry
from app.services.bootstrap import (
    bootstrap_browser_accounts,
    bootstrap_env_account,
    bootstrap_registry,
)


class TestBootstrapRegistry(unittest.IsolatedAsyncioTestCase):
    async def test_completes_on_empty_machine(self):
        """No env cookies + no browser discovery → registry stays empty, no
        exception raised. The chat path will 404 on every request until cookies
        arrive, but boot itself is healthy."""
        reg = AccountRegistry()
        with patch("app.services.bootstrap.bootstrap_env_account"), \
             patch("app.services.bootstrap.bootstrap_browser_accounts",
                   new=AsyncMock(return_value=0)):
            # Must not raise.
            await bootstrap_registry(reg)
        self.assertEqual(len(reg), 0)

    async def test_runs_env_then_browser_in_order(self):
        """The order matters: env_account first (so manual `env:0` wins on
        identity collisions), then browser discovery."""
        reg = AccountRegistry()
        order: list[str] = []

        def fake_env(_reg):
            order.append("env")

        async def fake_browser(_reg):
            order.append("browser")
            return 0

        with patch("app.services.bootstrap.bootstrap_env_account", side_effect=fake_env), \
             patch("app.services.bootstrap.bootstrap_browser_accounts",
                   new=AsyncMock(side_effect=fake_browser)):
            await bootstrap_registry(reg)

        self.assertEqual(order, ["env", "browser"])


class TestBootstrapEnvAccount(unittest.TestCase):
    """`bootstrap_env_account` is the only path that creates `env:0` from
    config.ini cookies (not env vars only — the file is the common case).
    Was previously only exercised through the `bootstrap_registry` integration
    test where it's mocked out — the wiring to CONFIG was untested."""

    def test_inserts_env_zero_when_config_has_cookies(self):
        reg = AccountRegistry()
        fake_config = {
            "Cookies": {"gemini_cookie_1psid": "psid-cfg", "gemini_cookie_1psidts": "psidts-cfg"},
            "Gemini": {"gem_id": ""},
        }
        with patch("app.services.bootstrap.CONFIG", fake_config), \
             patch("app.services.bootstrap.settings.cookie_1psid_env", return_value=None), \
             patch("app.services.bootstrap.settings.cookie_1psidts_env", return_value=None), \
             patch("app.services.bootstrap.settings.account_index_env", return_value=None), \
             patch("app.services.bootstrap.settings.initial_gem_id_env", return_value=None):
            bootstrap_env_account(reg)
        a = reg.get("env:0")
        self.assertIsNotNone(a)
        self.assertEqual(a.psid, "psid-cfg")
        self.assertEqual(a.psidts, "psidts-cfg")
        self.assertEqual(a.source, "env")

    def test_noop_when_no_cookies_anywhere(self):
        # Empty config + no env vars → silently skip. Boot must still be clean.
        reg = AccountRegistry()
        fake_config = {"Cookies": {"gemini_cookie_1psid": "", "gemini_cookie_1psidts": ""}}
        with patch("app.services.bootstrap.CONFIG", fake_config), \
             patch("app.services.bootstrap.settings.cookie_1psid_env", return_value=None), \
             patch("app.services.bootstrap.settings.cookie_1psidts_env", return_value=None):
            bootstrap_env_account(reg)
        self.assertIsNone(reg.get("env:0"))


class TestBootstrapBrowserAccountsClosesOrphan(unittest.IsolatedAsyncioTestCase):
    """Cross-browser refresh must close the previous client when cookies just
    rotated, otherwise the lib's auto_refresh task keeps polling on stale
    1PSID."""

    async def test_orphan_client_closed_on_cookie_rotation(self):
        reg = AccountRegistry()
        # Seed firefox:0 with a stale client.
        old_client = AsyncMock()
        seeded = Account(
            id="firefox:0", source="browser",
            psid="OLD", psidts="OLD", account_index=0, email="a@x.com",
        )
        seeded.client = old_client
        reg.upsert(seeded)

        with patch("app.services.bootstrap.discover_accounts",
                   new=AsyncMock(return_value=[
                       {"id": "firefox:0", "browser": "firefox", "index": 0, "email": "a@x.com"},
                   ])), \
             patch("app.services.bootstrap.get_all_cookie_pairs",
                   return_value={"firefox": ("ROTATED", "ROTATED")}):
            count = await bootstrap_browser_accounts(reg)

        self.assertEqual(count, 1)
        old_client.close.assert_awaited_once()
        self.assertIsNone(reg.get("firefox:0").client)

    async def test_no_close_when_cookies_unchanged(self):
        reg = AccountRegistry()
        old_client = AsyncMock()
        seeded = Account(
            id="firefox:0", source="browser",
            psid="SAME", psidts="SAME", account_index=0, email="a@x.com",
        )
        seeded.client = old_client
        reg.upsert(seeded)

        with patch("app.services.bootstrap.discover_accounts",
                   new=AsyncMock(return_value=[
                       {"id": "firefox:0", "browser": "firefox", "index": 0, "email": "a@x.com"},
                   ])), \
             patch("app.services.bootstrap.get_all_cookie_pairs",
                   return_value={"firefox": ("SAME", "SAME")}):
            await bootstrap_browser_accounts(reg)

        old_client.close.assert_not_awaited()
        # Client preserved across the no-op upsert.
        self.assertIs(reg.get("firefox:0").client, old_client)


if __name__ == "__main__":
    unittest.main()
