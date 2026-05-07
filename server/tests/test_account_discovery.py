"""Tests for the cross-browser account discovery service and the
`/accounts/*` HTTP surface that drives the headless multi-account UX.

Mocks browser cookies + httpx at the boundary so tests don't touch the real
browser_cookie3 backends or hit gemini.google.com."""
import unittest
from unittest.mock import AsyncMock, patch

from _helpers import install_registry, seeded_registry
from app.main import app
from app.services.account_discovery import (
    discover_accounts,
    parse_account_id,
    probe_gemini_account,
    resolve_session_for_account_id,
)
from app.services.account_registry import AccountRegistry
from litestar.testing import TestClient


class TestParseAccountId(unittest.TestCase):
    def test_well_formed(self):
        self.assertEqual(parse_account_id("firefox:0"), ("firefox", 0))
        self.assertEqual(parse_account_id("brave:7"), ("brave", 7))

    def test_missing_colon_returns_none(self):
        self.assertIsNone(parse_account_id("firefox"))

    def test_non_int_index_returns_none(self):
        self.assertIsNone(parse_account_id("firefox:zero"))

    def test_index_out_of_range_returns_none(self):
        # Google chains at most 8 accounts → 0..7; everything else is a typo
        # or a malicious value we shouldn't pass to the URL router.
        self.assertIsNone(parse_account_id("firefox:8"))
        self.assertIsNone(parse_account_id("firefox:-1"))

    def test_empty_browser_returns_none(self):
        self.assertIsNone(parse_account_id(":1"))


class TestResolveSession(unittest.TestCase):
    def test_unknown_browser_returns_none(self):
        with patch("app.services.account_discovery.get_all_cookie_pairs", return_value={}):
            self.assertIsNone(resolve_session_for_account_id("firefox:0"))

    def test_returns_psid_psidts_index_for_known_browser(self):
        with patch(
            "app.services.account_discovery.get_all_cookie_pairs",
            return_value={"chrome": ("psid-c", "psidts-c")},
        ):
            self.assertEqual(
                resolve_session_for_account_id("chrome:3"),
                ("psid-c", "psidts-c", 3),
            )

    def test_malformed_id_returns_none(self):
        with patch("app.services.account_discovery.get_all_cookie_pairs", return_value={"firefox": ("p", "pts")}):
            self.assertIsNone(resolve_session_for_account_id("garbage"))


class TestProbeGeminiAccount(unittest.IsolatedAsyncioTestCase):
    """The email regex is the only fragile surface that touches Gemini's HTML
    directly — Google rewrote the markup once between v0.1.0 and v0.1.1
    (plain text → JSON-quoted), silently breaking `/auth/accounts/`. Pin the
    JSON-quoted form here so a future regex regression is caught at unit-test
    time instead of via a silent prod failure."""

    @staticmethod
    def _fake_response(status: int, body: str, path: str = "/u/3/app"):
        from types import SimpleNamespace
        return SimpleNamespace(
            status_code=status,
            text=body,
            url=SimpleNamespace(path=path),
        )

    async def test_extracts_email_from_json_quoted_html(self):
        # Decoy email appears unquoted earlier in the page (Google embeds
        # `mailto:foo@x.com` and similar non-user references). The probe MUST
        # ignore the unquoted form and only return the JSON-quoted user email
        # — that's the regex contract that broke in v0.1.0 → v0.1.1.
        body = (
            'mailto:decoy@notuser.com '
            'href="https://gemini.google.com/" '
            'lots of html ... "alice@example.com" ... more html'
        )
        client = AsyncMock()
        client.get = AsyncMock(return_value=self._fake_response(200, body, "/u/3/app"))
        self.assertEqual(await probe_gemini_account(client, 3), "alice@example.com")

    async def test_filters_service_emails(self):
        # Gemini embeds noreply@/...@google.com/...@gemini.google.com strings;
        # only a real user email should survive `_is_user_email`.
        body = (
            '"noreply-foo@google.com" "alice@google.com" '
            '"bot@gemini.google.com" "real@example.com"'
        )
        client = AsyncMock()
        client.get = AsyncMock(return_value=self._fake_response(200, body, "/u/2/app"))
        self.assertEqual(await probe_gemini_account(client, 2), "real@example.com")

    async def test_redirect_to_u0_returns_none(self):
        # Empty slot: Google redirects /u/N (N>0) to /u/0/app — caller must
        # treat it as "no account here", not "account = u/0".
        client = AsyncMock()
        client.get = AsyncMock(return_value=self._fake_response(200, '"x@y.com"', "/u/0/app"))
        self.assertIsNone(await probe_gemini_account(client, 5))


class TestDiscoverAccounts(unittest.IsolatedAsyncioTestCase):
    """Top-level service: combines cookie discovery + per-session /u/N probes
    into a flat list with stable ids."""

    async def test_no_browsers_with_cookies_returns_empty(self):
        with patch("app.services.account_discovery.get_all_cookie_pairs", return_value={}):
            self.assertEqual(await discover_accounts(), [])

    async def test_aggregates_one_browser_with_two_chained_accounts(self):
        # `probe_gemini_account` is the per-/u/N email scraper. We stub it to model
        # a session that has u/0 + u/1 chained, and u/2..7 empty/mirrored.
        async def fake_probe(client, idx):
            return {0: "alice@x.com", 1: "alice@work.com"}.get(idx)

        with patch("app.services.account_discovery.get_all_cookie_pairs",
                   return_value={"firefox": ("psid-f", "psidts-f")}), \
             patch("app.services.account_discovery.probe_gemini_account", side_effect=fake_probe):
            accounts = await discover_accounts()

        self.assertEqual(accounts, [
            {"id": "firefox:0", "browser": "firefox", "index": 0, "email": "alice@x.com"},
            {"id": "firefox:1", "browser": "firefox", "index": 1, "email": "alice@work.com"},
        ])

    async def test_aggregates_across_multiple_browsers(self):
        async def fake_probe(client, idx):
            # Probed cookies decide the email; we look at the client's cookies
            # to know which browser session we're in.
            psid = client.cookies.get("__Secure-1PSID")
            if psid == "psid-f":
                return {0: "ff@x.com"}.get(idx)
            if psid == "psid-c":
                return {0: "ch@y.com", 1: "ch2@y.com"}.get(idx)
            return None

        with patch("app.services.account_discovery.get_all_cookie_pairs",
                   return_value={
                       "firefox": ("psid-f", "psidts-f"),
                       "chrome": ("psid-c", "psidts-c"),
                   }), \
             patch("app.services.account_discovery.probe_gemini_account", side_effect=fake_probe):
            accounts = await discover_accounts()

        # Order between browsers is parallel-discovery-dependent, so check the set.
        ids = {a["id"] for a in accounts}
        self.assertEqual(ids, {"firefox:0", "chrome:0", "chrome:1"})

    async def test_one_browser_failing_does_not_block_others(self):
        async def fake_discover(browser, psid, psidts):
            if browser == "bad":
                raise RuntimeError("simulated browser_cookie3 crash")
            return [{"id": f"{browser}:0", "browser": browser, "index": 0, "email": "ok@x.com"}]

        with patch("app.services.account_discovery.get_all_cookie_pairs",
                   return_value={
                       "good": ("psid-good", "psidts-good"),
                       "bad": ("psid-bad", "psidts-bad"),
                   }), \
             patch("app.services.account_discovery._discover_browser", side_effect=fake_discover):
            accounts = await discover_accounts()

        self.assertEqual({a["id"] for a in accounts}, {"good:0"})

    async def test_repeated_email_breaks_loop(self):
        """Google serves the /u/0 page for unused chained slots, so the same
        email reappearing means we've fallen off the real account list."""
        async def fake_probe(client, idx):
            return "alice@x.com" if idx in (0, 1, 2) else None

        with patch("app.services.account_discovery.get_all_cookie_pairs",
                   return_value={"firefox": ("p", "pts")}), \
             patch("app.services.account_discovery.probe_gemini_account", side_effect=fake_probe):
            accounts = await discover_accounts()
        # Only u/0 should be returned even though u/1 and u/2 also "have" the email.
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["id"], "firefox:0")


class TestAccountsEndpoints(unittest.TestCase):
    """`AccountsController` is a thin read/write layer over the registry. We
    install a registry per test instead of mocking browser cookies — the chat
    + auth flows are exercised end-to-end against the real registry code."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_list_returns_registered_accounts(self):
        with install_registry(seeded_registry(account_count=2)):
            r = self.client.get("/accounts/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ids = {a["id"] for a in body}
        self.assertEqual(ids, {"firefox:0", "firefox:1"})
        # No `is_default` field — the concept was removed (routing is always
        # explicit per request).
        for a in body:
            self.assertNotIn("is_default", a)

    def test_list_empty_registry_returns_empty_list(self):
        with install_registry(AccountRegistry()):
            r = self.client.get("/accounts/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_refresh_runs_bootstrap_and_reports_added(self):
        # /accounts/refresh delegates to bootstrap_browser_accounts; verify
        # the endpoint reports the diff (added ids) without exercising the real
        # cross-browser discovery.
        from app.services.account_registry import Account

        reg = seeded_registry()  # firefox:0

        async def fake_bootstrap(registry):
            registry.upsert(Account(
                id="chrome:0", source="browser",
                psid="p", psidts="pts", account_index=0, email="ch@x.com",
            ))
            return 1

        with install_registry(reg), \
             patch("app.endpoints.accounts.bootstrap_browser_accounts",
                   new=AsyncMock(side_effect=fake_bootstrap)):
            r = self.client.post("/accounts/refresh")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["upserted"], 1)
        self.assertEqual(body["added"], ["chrome:0"])
        self.assertEqual(body["total"], 2)


if __name__ == "__main__":
    unittest.main()
