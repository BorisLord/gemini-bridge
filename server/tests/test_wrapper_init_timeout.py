"""`BridgeGeminiClient.init` MUST forward `settings.REQUEST_TIMEOUT_SECONDS`
to the lib's `client.init(timeout=...)`. Without this, the lib falls back to
its 450s default and the bridge's intended cap is silently ignored."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import settings
from app.services.gemini_wrapper import BridgeGeminiClient


class TestBridgeInitForwardsTimeout(unittest.IsolatedAsyncioTestCase):
    async def test_init_passes_request_timeout_to_lib(self):
        with patch("app.services.gemini_wrapper.WebGeminiClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.init = AsyncMock()
            MockClient.return_value = mock_instance

            client = BridgeGeminiClient(secure_1psid="p", secure_1psidts="pts")
            with patch.object(client, "_persist_cookies", new=AsyncMock()):
                await client.init()

            mock_instance.init.assert_awaited_once_with(timeout=settings.REQUEST_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
