"""Tests for the cross-browser account discovery service and the
`/accounts/*` HTTP surface that drives the headless multi-account UX.

Mocks browser cookies + httpx at the boundary so tests don't touch the real
browser_cookie3 backends or hit gemini.google.com."""
import unittest
from unittest.mock import AsyncMock, patch

from app.main import app
from app.services.account_discovery import (
    discover_accounts,
    parse_account_id,
    resolve_session_for_account_id,
)
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

    def test_non_string_returns_none(self):
        # Defensive: callers always pass str (Pydantic-validated), but a future
        # internal caller passing an int / None must not crash the resolver.
        self.assertIsNone(parse_account_id(None))  # type: ignore[arg-type]
        self.assertIsNone(parse_account_id(42))    # type: ignore[arg-type]


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
    """`AccountsController` glues the service to HTTP. Mock the service, not
    the underlying browser/httpx — this layer is just routing + persistence."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_list_returns_discovered_accounts(self):
        sample = [{"id": "firefox:0", "browser": "firefox", "index": 0, "email": "a@x.com"}]
        with patch("app.endpoints.auth.discover_accounts", new=AsyncMock(return_value=sample)):
            r = self.client.get("/accounts/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), sample)

    def test_use_account_switches_and_persists(self):
        with patch("app.endpoints.auth.get_all_cookie_pairs",
                   return_value={"firefox": ("psid-x", "psidts-x")}), \
             patch("app.endpoints.auth.refresh_gemini_client",
                   new=AsyncMock(return_value="refreshed")) as refresh_mock, \
             patch("app.endpoints.auth.persist_selected_account_id") as persist_mock:
            r = self.client.post("/accounts/use", json={"id": "firefox:0"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok", "id": "firefox:0", "deduped": False})
        refresh_mock.assert_awaited_once_with("psid-x", "psidts-x", account_index=0)
        persist_mock.assert_called_once_with("firefox:0")

    def test_use_account_404_when_browser_session_gone(self):
        with patch("app.endpoints.auth.get_all_cookie_pairs", return_value={"chrome": ("p", "pts")}), \
             patch("app.endpoints.auth.refresh_gemini_client", new=AsyncMock()) as refresh_mock, \
             patch("app.endpoints.auth.persist_selected_account_id") as persist_mock:
            r = self.client.post("/accounts/use", json={"id": "firefox:0"})
        self.assertEqual(r.status_code, 404)
        refresh_mock.assert_not_awaited()
        persist_mock.assert_not_called()

    def test_use_account_400_on_malformed_id(self):
        r = self.client.post("/accounts/use", json={"id": "garbage"})
        self.assertEqual(r.status_code, 400)

    def test_use_account_502_on_auth_failure(self):
        with patch("app.endpoints.auth.get_all_cookie_pairs",
                   return_value={"firefox": ("p", "pts")}), \
             patch("app.endpoints.auth.refresh_gemini_client",
                   new=AsyncMock(return_value="failed")), \
             patch("app.endpoints.auth.persist_selected_account_id") as persist_mock:
            r = self.client.post("/accounts/use", json={"id": "firefox:0"})
        self.assertEqual(r.status_code, 502)
        # Must NOT persist a failed selection — boot would then loop on a bad cookie pair.
        persist_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
