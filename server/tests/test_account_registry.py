"""`AccountRegistry` is the in-process source of truth for which Gemini
accounts the bridge can serve. Behaviour to lock in:

  * upsert overwrites the same id, preserves cached lib client iff the
    cookies didn't change, returns the orphan Account when they did
  * no "default account" — every routing decision is explicit per request
  * get_or_init_client lazy-instantiates exactly once per id, even under
    concurrent first-use (TOCTOU guarded by a per-account lock)
  * close_all stops every active client at shutdown

The lifecycle (auto_close, transparent re-init, 1PSIDTS rotation) is
delegated to gemini-webapi itself, so this layer only owns selection."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.account_registry import Account, AccountRegistry


def _make(id_: str = "firefox:0", **overrides) -> Account:
    base = {
        "id": id_,
        "source": "browser",
        "psid": "p",
        "psidts": "pts",
        "account_index": int(id_.rsplit(":", 1)[1]) if ":" in id_ else 0,
        "email": f"{id_.replace(':', '-')}@x.com",
    }
    base.update(overrides)
    return Account(**base)


class TestUpsert(unittest.TestCase):
    def test_first_insert(self):
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))
        self.assertEqual(len(reg), 1)
        self.assertIn("firefox:0", reg)

    def test_overwrite_same_id_keeps_lazy_client_when_cookies_unchanged(self):
        reg = AccountRegistry()
        first = _make("firefox:0")
        first.client = "<existing-client-marker>"  # any non-None sentinel
        reg.upsert(first)
        # Re-upsert with the same psid/psidts/index — the cached client must survive.
        again = _make("firefox:0")
        reg.upsert(again)
        self.assertEqual(reg.get("firefox:0").client, "<existing-client-marker>")

    def test_overwrite_drops_client_when_cookies_change(self):
        reg = AccountRegistry()
        first = _make("firefox:0", psid="old-p")
        first.client = "<existing>"
        reg.upsert(first)
        reg.upsert(_make("firefox:0", psid="rotated-p"))
        # Credentials shifted → cached client invalidated; next get_or_init re-builds.
        self.assertIsNone(reg.get("firefox:0").client)

    def test_overwrite_returns_orphan_when_cookies_change(self):
        # The async caller (extension push, /accounts/refresh) needs the prior
        # Account back so it can `await old.client.close()` and stop the lib's
        # auto_refresh / auto_close background tasks on stale credentials.
        reg = AccountRegistry()
        first = _make("firefox:0", psid="old-p")
        first.client = "<the-orphan-client-marker>"
        self.assertIsNone(reg.upsert(first))  # first insert: nothing to orphan
        orphan = reg.upsert(_make("firefox:0", psid="rotated-p"))
        self.assertIsNotNone(orphan)
        self.assertEqual(orphan.client, "<the-orphan-client-marker>")

    def test_overwrite_returns_none_when_cookies_unchanged(self):
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))
        # Same cookie sig → no orphan to close.
        self.assertIsNone(reg.upsert(_make("firefox:0")))

    def test_overwrite_preserves_per_account_state_when_unchanged(self):
        reg = AccountRegistry()
        first = _make("firefox:0", selected_gem_id="gem-abc", email="a@b.com")
        reg.upsert(first)
        # Re-upsert without selected_gem_id / email — must not wipe them.
        reg.upsert(_make("firefox:0", selected_gem_id=None, email=None))
        self.assertEqual(reg.get("firefox:0").selected_gem_id, "gem-abc")
        self.assertEqual(reg.get("firefox:0").email, "a@b.com")


class TestLookup(unittest.TestCase):
    def test_get_unknown_returns_none(self):
        self.assertIsNone(AccountRegistry().get("nope:99"))

    def test_list_returns_all(self):
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))
        reg.upsert(_make("env:0", source="env"))
        ids = sorted(a.id for a in reg.list())
        self.assertEqual(ids, ["env:0", "firefox:0"])


class TestNoDefaultConcept(unittest.TestCase):
    """The `default-account` notion was removed in favour of always-explicit
    routing. These tests lock in that the registry exposes nothing default-
    related — any future re-introduction must update this expectation."""

    def test_no_default_attributes(self):
        reg = AccountRegistry()
        for attr in ("default", "default_id", "set_default", "_default_id"):
            self.assertFalse(
                hasattr(reg, attr),
                f"AccountRegistry should not expose `{attr}` — routing is explicit per request",
            )


class TestGetOrInitClient(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_init_then_cached(self):
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))
        with patch("app.services.account_registry.AccountRoutedGeminiClient") as MockCls:
            # Each instance has its own AsyncMock init() so we can assert it's
            # called exactly once.
            instance = MockCls.return_value
            instance.init = AsyncMock()
            first = await reg.get_or_init_client("firefox:0")
            second = await reg.get_or_init_client("firefox:0")
        self.assertIs(first, second)
        instance.init.assert_awaited_once()

    async def test_unknown_id_raises_keyerror(self):
        reg = AccountRegistry()
        with self.assertRaises(KeyError):
            await reg.get_or_init_client("nope:99")

    async def test_init_kwargs_carry_lib_lifecycle_knobs(self):
        """The lib's auto_close/auto_refresh do the work — verify the kwargs land."""
        from app import settings
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))
        with patch("app.services.account_registry.AccountRoutedGeminiClient") as MockCls:
            instance = MockCls.return_value
            instance.init = AsyncMock()
            await reg.get_or_init_client("firefox:0")
        called_kwargs = instance.init.await_args.kwargs
        self.assertEqual(called_kwargs["timeout"], settings.REQUEST_TIMEOUT_SECONDS)
        self.assertTrue(called_kwargs["auto_close"])
        self.assertEqual(called_kwargs["close_delay"], settings.ACCOUNT_IDLE_CLOSE_SECONDS)
        self.assertTrue(called_kwargs["auto_refresh"])

    async def test_concurrent_first_use_inits_only_once(self):
        """Two coros calling `get_or_init_client` simultaneously must not both
        construct + assign a client. Without the per-account lock, the
        sequence (check→assign→await init) interleaves: the second coro sees
        the first's partially-built `account.client` and returns it before
        init() has completed."""
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))

        init_calls = 0
        ctor_calls = 0

        async def slow_init(*args, **kwargs):
            # Simulate the real lib's network round-trips: yield enough times
            # for a contending coro to make progress.
            nonlocal init_calls
            init_calls += 1
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        def fake_ctor(*args, **kwargs):
            nonlocal ctor_calls
            ctor_calls += 1
            inst = MagicMock()
            inst.init = AsyncMock(side_effect=slow_init)
            return inst

        with patch("app.services.account_registry.AccountRoutedGeminiClient", side_effect=fake_ctor):
            results = await asyncio.gather(
                reg.get_or_init_client("firefox:0"),
                reg.get_or_init_client("firefox:0"),
                reg.get_or_init_client("firefox:0"),
            )
        # Same client returned to all three.
        self.assertIs(results[0], results[1])
        self.assertIs(results[1], results[2])
        # Crucially: ctor + init each ran exactly once.
        self.assertEqual(ctor_calls, 1)
        self.assertEqual(init_calls, 1)


class TestCloseAll(unittest.IsolatedAsyncioTestCase):
    """Graceful shutdown: registry.close_all() must call close() on every
    active client so the lib's auto_refresh / auto_close background tasks
    don't dangle past the lifespan exit."""

    async def test_close_all_calls_close_on_each_active_client(self):
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))
        reg.upsert(_make("firefox:1"))
        c0 = MagicMock()
        c0.close = AsyncMock()
        c1 = MagicMock()
        c1.close = AsyncMock()
        reg.get("firefox:0").client = c0
        reg.get("firefox:1").client = c1

        await reg.close_all()

        c0.close.assert_awaited_once()
        c1.close.assert_awaited_once()

    async def test_close_all_skips_accounts_without_client(self):
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))  # client is None — never initialised
        # Must not raise.
        await reg.close_all()

    async def test_close_all_swallows_per_client_errors(self):
        # One client failing must not prevent the others from closing.
        reg = AccountRegistry()
        reg.upsert(_make("firefox:0"))
        reg.upsert(_make("firefox:1"))
        bad = MagicMock()
        bad.close = AsyncMock(side_effect=RuntimeError("boom"))
        good = MagicMock()
        good.close = AsyncMock()
        reg.get("firefox:0").client = bad
        reg.get("firefox:1").client = good

        await reg.close_all()  # must not raise

        good.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
