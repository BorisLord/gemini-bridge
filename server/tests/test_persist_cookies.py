"""Cookie persistence in BridgeGeminiClient._persist_cookies:
the temp file written before atomic-replace MUST be chmodded to 0o600
since it contains Google session cookies that are password-equivalent."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.gemini_wrapper import BridgeGeminiClient


class TestPersistCookiesPermissions(unittest.IsolatedAsyncioTestCase):
    async def test_persist_cookies_chmods_tmp_to_0600(self):
        with TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.conf"
            # The wrapper aborts early if config.conf doesn't exist.
            cfg_path.write_text("[Cookies]\n")

            client = BridgeGeminiClient.__new__(BridgeGeminiClient)
            client.client = MagicMock()
            client.client.cookies = {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "psidts"}
            client.client.init = AsyncMock()
            client.account_index = 0

            with patch("app.services.gemini_wrapper._CONFIG_PATH", cfg_path):
                await client._persist_cookies()

            # Final file should be owner-only readable/writable.
            mode = cfg_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600, f"expected 0o600, got {oct(mode)}")


if __name__ == "__main__":
    unittest.main()
