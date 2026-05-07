"""`AccountRoutedGeminiClient` is a subclass of `gemini_webapi.GeminiClient`
whose `init()` re-installs the `/u/{N}/` monkey-patch on the underlying
AsyncSession every time. This is load-bearing because the lib's `@running`
decorator silently re-inits the client when `auto_close` fires — it rebuilds
`self.client` from scratch and our patch would be gone otherwise.

The whole point of this test: simulate a 2nd init() (the @running re-init)
and verify the route patch still sticks."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.account_routed_client import AccountRoutedGeminiClient, _inject_account_index


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


class TestInjectAccountIndex(unittest.TestCase):
    """Unit-level coverage of the URL rewrite — the routing wrapper closes
    over this function, so its correctness IS the routing contract. Without
    these checks, a regression here would only surface as silent
    wrong-account traffic at runtime."""

    def test_rewrites_gemini_url_with_user_slot(self):
        self.assertEqual(
            _inject_account_index("https://gemini.google.com/_/BardChatUi/data", 4),
            "https://gemini.google.com/u/4/_/BardChatUi/data",
        )

    def test_idempotent_when_url_already_prefixed(self):
        # Re-wrapping after auto_close re-init must not stack two /u/N/ slots.
        self.assertEqual(
            _inject_account_index("https://gemini.google.com/u/4/_/x", 4),
            "https://gemini.google.com/u/4/_/x",
        )

    def test_account_index_zero_is_passthrough(self):
        self.assertEqual(
            _inject_account_index("https://gemini.google.com/_/x", 0),
            "https://gemini.google.com/_/x",
        )

    def test_foreign_host_untouched(self):
        # Login flow / auxiliary Google domains must not get /u/N injected.
        self.assertEqual(
            _inject_account_index("https://accounts.google.com/oauth", 4),
            "https://accounts.google.com/oauth",
        )


if __name__ == "__main__":
    unittest.main()
