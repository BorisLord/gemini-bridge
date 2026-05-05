"""Per-request account routing in `/v1/chat/completions`. The contract:

  1. `X-Bridge-Account` header       (most explicit, wins on conflict)
  2. `model@<account_id>` suffix     (universal — works in any client that
                                      passes the model name through)
  3. otherwise → 400                 (no implicit fallback to a default —
                                      we refuse to guess which Google account
                                      a request should be charged to)

Plus the failure modes:
  * unknown id → 404 (typo / browser session gone)
  * `model@bad-id` (no colon in suffix) → not interpreted as routing, so
    no header + bare-ish model → 400 too. Lets `something@example.com`-style
    model names survive a future schema change without being shadowed."""
import unittest
from unittest.mock import AsyncMock, MagicMock

from _helpers import install_registry
from app.main import app
from app.services.account_registry import Account, AccountRegistry
from litestar.testing import TestClient


def _seed_registry() -> AccountRegistry:
    reg = AccountRegistry()
    for idx in (0, 1):
        reg.upsert(Account(
            id=f"firefox:{idx}",
            source="browser",
            psid=f"p{idx}",
            psidts=f"pts{idx}",
            account_index=idx,
            email=f"ff{idx}@x.com",
        ))
    return reg


def _fake_client(text: str = "ok") -> MagicMock:
    client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.text = text
    client.generate_content = AsyncMock(return_value=fake_resp)
    return client


def _bind_clients(reg: AccountRegistry) -> dict[str, MagicMock]:
    """Pre-attach a per-account mock so `get_or_init_client` short-circuits
    (account.client is not None) and we can assert which one was used."""
    clients: dict[str, MagicMock] = {}
    for a in reg.list():
        a.client = _fake_client(text=f"from-{a.id}")
        clients[a.id] = a.client
    return clients


class TestRoutingPrecedence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _post(self, body: dict, headers: dict | None = None):
        reg = _seed_registry()
        clients = _bind_clients(reg)
        with install_registry(reg):
            r = self.client.post("/v1/chat/completions", json=body, headers=headers or {})
        return r, clients

    def test_no_routing_returns_400(self):
        # Bare model with no header is rejected — we refuse to guess.
        r, clients = self._post(
            {"model": "gemini-3-pro", "messages": [{"role": "user", "content": "hi"}]}
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Routing required", r.json()["error"]["message"])
        clients["firefox:0"].generate_content.assert_not_awaited()
        clients["firefox:1"].generate_content.assert_not_awaited()

    def test_header_routes_to_explicit_account(self):
        r, clients = self._post(
            {"model": "gemini-3-pro", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Bridge-Account": "firefox:1"},
        )
        self.assertEqual(r.status_code, 200)
        clients["firefox:1"].generate_content.assert_awaited_once()
        clients["firefox:0"].generate_content.assert_not_awaited()

    def test_suffix_routes_to_explicit_account(self):
        r, clients = self._post(
            {"model": "gemini-3-pro@firefox:1", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(r.status_code, 200)
        clients["firefox:1"].generate_content.assert_awaited_once()
        # Bare model name forwarded to Gemini (suffix stripped before generate_content).
        called = clients["firefox:1"].generate_content.await_args
        self.assertEqual(called.kwargs.get("model"), "gemini-3-pro")

    def test_header_wins_over_suffix(self):
        r, clients = self._post(
            {"model": "gemini-3-pro@firefox:0", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Bridge-Account": "firefox:1"},
        )
        self.assertEqual(r.status_code, 200)
        clients["firefox:1"].generate_content.assert_awaited_once()
        clients["firefox:0"].generate_content.assert_not_awaited()

    def test_unknown_id_returns_404(self):
        r, _ = self._post(
            {"model": "gemini-3-pro", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Bridge-Account": "nope:99"},
        )
        self.assertEqual(r.status_code, 404)

    def test_suffix_without_colon_treated_as_part_of_model(self):
        # `model@something-without-colon` — not a registry id, NOT interpreted as
        # routing. With explicit-routing-only, this means no routing was
        # provided → 400 (defensive against vendor-prefixed model naming
        # conventions like `openai@gpt-4` if a client ever sends one).
        r, clients = self._post(
            {"model": "gemini-3-pro@noncolon", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(r.status_code, 400)
        clients["firefox:0"].generate_content.assert_not_awaited()


class TestModelsListAlwaysSuffixed(unittest.TestCase):
    """`GET /v1/models` only ever returns `<model>@<id>` entries — bare names
    would be unrouteable (chat path requires explicit routing) so listing them
    would mislead picker UIs."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_single_account_emits_only_suffixed_variants(self):
        reg = _seed_registry()
        # Drop firefox:1 to leave a single-account registry.
        reg._accounts.pop("firefox:1")
        with install_registry(reg):
            r = self.client.get("/v1/models")
        ids = {m["id"] for m in r.json()["data"]}
        # No bare names anywhere — every entry carries the @<id> suffix.
        self.assertTrue(all("@" in i for i in ids), f"bare names leaked: {[i for i in ids if '@' not in i]}")
        self.assertIn("gemini-3-pro@firefox:0", ids)

    def test_multi_account_emits_one_entry_per_model_per_account(self):
        reg = _seed_registry()
        with install_registry(reg):
            r = self.client.get("/v1/models")
        ids = {m["id"] for m in r.json()["data"]}
        self.assertNotIn("gemini-3-pro", ids)
        self.assertIn("gemini-3-pro@firefox:0", ids)
        self.assertIn("gemini-3-pro@firefox:1", ids)

    def test_empty_registry_returns_empty_list(self):
        with install_registry(AccountRegistry()):
            r = self.client.get("/v1/models")
        self.assertEqual(r.json()["data"], [])


if __name__ == "__main__":
    unittest.main()
