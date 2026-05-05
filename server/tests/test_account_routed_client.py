"""`AccountRoutedGeminiClient` is a subclass of `gemini_webapi.GeminiClient`
whose `init()` re-installs the `/u/{N}/` monkey-patch on the underlying
AsyncSession every time. This is load-bearing because the lib's `@running`
decorator silently re-inits the client when `auto_close` fires — it rebuilds
`self.client` from scratch and our patch would be gone otherwise.

The whole point of this test: simulate a 2nd init() (the @running re-init)
and verify the route patch still sticks."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.account_routed_client import AccountRoutedGeminiClient


class _FakeSession:
    """Stand-in for curl_cffi AsyncSession — only needs `request` (the thing
    we monkey-patch) and accept arbitrary attribute writes."""

    def __init__(self) -> None:
        self.request = AsyncMock()


def _patch_super_init(client: AccountRoutedGeminiClient, sessions: list[_FakeSession]):
    """Replace `WebGeminiClient.init` with a fake that swaps in a fresh
    AsyncSession every call — mirrors what the real lib does on (re-)init."""
    call_count = {"n": 0}

    async def fake_super_init(*args, **kwargs):
        client.client = sessions[call_count["n"]]
        call_count["n"] += 1

    return fake_super_init


class TestAccountRouterPatch(unittest.IsolatedAsyncioTestCase):
    async def test_router_installed_after_first_init(self):
        client = AccountRoutedGeminiClient(
            secure_1psid="p", secure_1psidts="pts", account_index=2,
        )
        session = _FakeSession()
        with patch(
            "app.services.account_routed_client.WebGeminiClient.init",
            new=_patch_super_init(client, [session]),
        ):
            await client.init()
        self.assertTrue(getattr(session, "_account_routed", False))

    async def test_router_reinstalled_after_second_init(self):
        """Simulates @running re-init after auto_close: a brand-new
        AsyncSession appears in self.client and our override must re-patch
        it. Without this guarantee, /u/N/ routing would silently break on
        long-idle accounts."""
        client = AccountRoutedGeminiClient(
            secure_1psid="p", secure_1psidts="pts", account_index=3,
        )
        sessions = [_FakeSession(), _FakeSession()]
        with patch(
            "app.services.account_routed_client.WebGeminiClient.init",
            new=_patch_super_init(client, sessions),
        ):
            await client.init()
            await client.init()
        self.assertTrue(getattr(sessions[0], "_account_routed", False))
        self.assertTrue(getattr(sessions[1], "_account_routed", False))
        # The second session is now active — verify the URL-injecting wrapper
        # is actually wired on it (not the original AsyncMock).
        self.assertNotEqual(sessions[1].request.__class__.__name__, "AsyncMock")

    async def test_no_router_when_account_index_zero(self):
        """account_index=0 is the default Google profile → /u/0 is implicit,
        no rewrite needed. The patch flag must not be set so we don't pay for
        the wrapper indirection on every request."""
        client = AccountRoutedGeminiClient(
            secure_1psid="p", secure_1psidts="pts", account_index=0,
        )
        session = _FakeSession()
        with patch(
            "app.services.account_routed_client.WebGeminiClient.init",
            new=_patch_super_init(client, [session]),
        ):
            await client.init()
        self.assertFalse(getattr(session, "_account_routed", False))

    async def test_extra_cookies_injected_after_super_init(self):
        """Workspace accounts need the full Google session jar (SID/HSID/
        SAPISID/...) — the lib only stores 1PSID/1PSIDTS by default. The
        injection must happen AFTER super().init() so it lands on the
        AsyncSession the lib just built."""
        client = AccountRoutedGeminiClient(
            secure_1psid="p", secure_1psidts="pts",
            account_index=0,
            extra_cookies={
                "__Secure-1PSID": "ignored",       # filtered out
                "__Secure-1PSIDTS": "ignored",     # filtered out
                "SAPISID": "sapi-val",
                "HSID": "hsid-val",
                "EMPTY": "",                        # filtered out
            },
        )
        captured = {}

        async def fake_super_init(*args, **kwargs):
            client.client = MagicMock()

        # Capture the cookies setter — `self.cookies = extras`.
        with patch(
            "app.services.account_routed_client.WebGeminiClient.init",
            new=fake_super_init,
        ), patch.object(
            type(client),
            "cookies",
            new_callable=lambda: property(lambda s: None, lambda s, v: captured.update(extras=v)),
        ):
            await client.init()
        self.assertEqual(captured["extras"], {"SAPISID": "sapi-val", "HSID": "hsid-val"})


if __name__ == "__main__":
    unittest.main()
