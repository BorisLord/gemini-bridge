"""Bridge contract: the lib's lifecycle (auto_close, auto_refresh, idle TTL)
is delegated entirely to `gemini-webapi`. The registry's `get_or_init_client`
must forward the corresponding kwargs into `AccountRoutedGeminiClient.init`,
otherwise the lib falls back to its own defaults and the bridge's tunables
(e.g. `ACCOUNT_IDLE_CLOSE_SECONDS`) are silently ignored."""
import unittest
from unittest.mock import AsyncMock, patch

from app import settings
from app.services.account_registry import Account, AccountRegistry


class TestRegistryForwardsLibLifecycleKwargs(unittest.IsolatedAsyncioTestCase):
    async def test_get_or_init_passes_lifecycle_kwargs(self):
        reg = AccountRegistry()
        reg.upsert(Account(
            id="firefox:0", source="browser",
            psid="p", psidts="pts", account_index=0,
        ))
        with patch("app.services.account_registry.AccountRoutedGeminiClient") as MockCls:
            instance = MockCls.return_value
            instance.init = AsyncMock()
            await reg.get_or_init_client("firefox:0")

        kwargs = instance.init.await_args.kwargs
        self.assertEqual(kwargs["timeout"], settings.REQUEST_TIMEOUT_SECONDS)
        self.assertTrue(kwargs["auto_close"])
        self.assertEqual(kwargs["close_delay"], settings.ACCOUNT_IDLE_CLOSE_SECONDS)
        self.assertTrue(kwargs["auto_refresh"])
        self.assertEqual(kwargs["refresh_interval"], settings.ACCOUNT_REFRESH_INTERVAL_SECONDS)


if __name__ == "__main__":
    unittest.main()
