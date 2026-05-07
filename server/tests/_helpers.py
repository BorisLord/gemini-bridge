"""Shared fixtures for the registry-based test suite.

The chat path now resolves accounts through `request.app.state.account_registry`.
Tests don't run the Litestar lifespan (no `with TestClient(app)`), so each test
that exercises chat/runtime/accounts endpoints installs its own registry on
`app.state` for the duration of the call.

Why a manual save/restore instead of `mock.patch.object`: Litestar's `State`
backs attributes via `__getattr__`/`_state` (not `__dict__`). `patch.object`
sees `is_local=False`, so on exit it calls `delattr(state, name)` and the
attribute is gone for every subsequent test. Manual setattr/setattr is the
only safe way to swap values on State."""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

from app.main import app
from app.services.account_registry import Account, AccountRegistry


def make_account(id_: str = "firefox:0", **overrides) -> Account:
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


def fake_client(text: str = "ok", thoughts: str | None = None) -> MagicMock:
    """Stand-in for `AccountRoutedGeminiClient` — only `generate_content` is
    exercised by the chat path. Explicit `thoughts` (default None) keeps
    MagicMock from auto-spawning a truthy attribute that would mis-trigger
    the reasoning_content path."""
    c = MagicMock()
    resp = MagicMock()
    resp.text = text
    resp.thoughts = thoughts
    c.generate_content = AsyncMock(return_value=resp)
    return c


def seeded_registry(
    account_count: int = 1,
    with_clients: bool = True,
    response_text: str = "ok",
    response_thoughts: str | None = None,
) -> AccountRegistry:
    """Build a registry with N firefox accounts, optionally pre-attached with
    mock clients so `get_or_init_client` returns them without hitting Google."""
    reg = AccountRegistry()
    for idx in range(account_count):
        a = make_account(f"firefox:{idx}")
        if with_clients:
            a.client = fake_client(text=response_text, thoughts=response_thoughts)
        reg.upsert(a)
    return reg


@contextmanager
def install_registry(reg: AccountRegistry):
    """Temporarily mount `reg` on `app.state.account_registry`. Restores the
    previous value (the default empty registry from main.py) on exit."""
    previous = app.state.account_registry
    app.state.account_registry = reg
    try:
        yield reg
    finally:
        app.state.account_registry = previous
