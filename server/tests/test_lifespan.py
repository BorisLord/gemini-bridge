"""Lifespan contract: `_lifespan` populates `app.state.account_registry`
from `bootstrap_registry`, then `close_all()` runs on shutdown.

Without the close_all step, per-account `auto_refresh` / `auto_close` asyncio
tasks survive the loop and emit "pending task" warnings at shutdown — and
worse, may keep polling Google after the process is supposed to be gone."""
import unittest
from unittest.mock import AsyncMock, patch

from app.main import _lifespan, app


class TestLifespan(unittest.IsolatedAsyncioTestCase):
    async def test_populates_registry_then_closes_all_on_shutdown(self):
        seen = {"close_called": False, "registry_after_bootstrap": None}

        async def fake_bootstrap(reg):
            # Mimic a successful boot: stash a sentinel account so we can
            # verify the registry is the one the lifespan actually mounted
            # on app.state.
            from app.services.account_registry import Account
            reg.upsert(Account(
                id="env:0", source="env", psid="p", psidts="pts", account_index=0,
            ))

        async def fake_close_all(self):
            del self  # bound method shape, instance unused
            seen["close_called"] = True

        with patch("app.main.bootstrap_registry", new=AsyncMock(side_effect=fake_bootstrap)), \
             patch("app.services.account_registry.AccountRegistry.close_all",
                   new=fake_close_all):
            async with _lifespan(app):
                # Inside the yield: registry mounted + bootstrap ran.
                reg = app.state.account_registry
                seen["registry_after_bootstrap"] = sorted(a.id for a in reg.list())
            # After the yield: close_all must have been awaited.

        self.assertEqual(seen["registry_after_bootstrap"], ["env:0"])
        self.assertTrue(seen["close_called"], "close_all not called at shutdown")


if __name__ == "__main__":
    unittest.main()
