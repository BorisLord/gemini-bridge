"""In-process registry of Gemini accounts (browser-discovered, extension-
pushed, env-bootstrapped) under stable ids like `firefox:0`, `extension:1`,
`env:0`. Each account holds cookies + a lazy `AccountRoutedGeminiClient`;
lifecycle (idle close, re-init, 1PSIDTS rotation) is delegated to the lib.

Lives on `app.state.account_registry` — single-worker uvicorn mandatory
(per-worker registries would diverge)."""
import asyncio
from dataclasses import dataclass
from typing import Literal

from app import settings
from app.config import CONFIG
from app.logger import logger
from app.services.account_routed_client import AccountRoutedGeminiClient

AccountSource = Literal["browser", "extension", "env"]


@dataclass
class Account:
    id: str                                   # "firefox:0", "extension:1", "env:0"
    source: AccountSource
    psid: str
    psidts: str
    account_index: int                        # /u/N
    email: str | None = None                  # filled by probe when available
    extra_cookies: dict | None = None
    selected_gem_id: str | None = None        # per-account Gem (RAM-only v1)
    client: AccountRoutedGeminiClient | None = None  # lazy init

    def cookie_signature(self) -> tuple:
        """Used to detect when an upsert is a real cookie change vs a no-op."""
        extras = tuple(sorted((self.extra_cookies or {}).items()))
        return (self.psid, self.psidts, self.account_index, extras)


class AccountRegistry:
    """Map of account id -> Account. Routing is always explicit (header or
    `model@<id>` suffix); there is no "current/default" account on the server."""

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}
        # One lock per account id, lazily created — guards `get_or_init_client`
        # against concurrent first-use of the same account (TOCTOU between the
        # `client is None` check and the awaited init).
        self._init_locks: dict[str, asyncio.Lock] = {}

    def upsert(self, account: Account) -> Account | None:
        """Add or replace. Returns the previous `Account` when it was replaced
        with rotated credentials (callers in async context should `await
        old.client.close()` to stop the lib's auto_refresh / auto_close
        background tasks). Returns None for first-time inserts and for no-op
        re-upserts (cookies unchanged)."""
        existing = self._accounts.get(account.id)
        orphan: Account | None = None
        if existing and existing.cookie_signature() != account.cookie_signature():
            # Credentials changed — invalidate the cached client and return
            # the old Account so the caller can close the orphan client.
            account.client = None
            orphan = existing
        elif existing:
            # No credential change — preserve lazy client + per-account state.
            account.client = existing.client
            if account.selected_gem_id is None:
                account.selected_gem_id = existing.selected_gem_id
            if account.email is None:
                account.email = existing.email
        self._accounts[account.id] = account
        return orphan

    def get(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def list(self) -> list[Account]:
        return list(self._accounts.values())

    def __len__(self) -> int:
        return len(self._accounts)

    def __contains__(self, account_id: str) -> bool:
        return account_id in self._accounts

    async def get_or_init_client(self, account_id: str) -> AccountRoutedGeminiClient:
        """Lazy-init under a per-account lock with double-check (TOCTOU between
        the `is None` check and the awaited init). The lib's `@running`
        transparently re-inits later if `auto_close` fires."""
        account = self._accounts.get(account_id)
        if account is None:
            raise KeyError(account_id)
        if account.client is not None:
            return account.client
        lock = self._init_locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            # Re-check after acquiring — a sibling coro may have built the
            # client while we were queued on the lock.
            if account.client is not None:
                return account.client
            proxy = CONFIG["Proxy"].get("http_proxy") or None
            new_client = AccountRoutedGeminiClient(
                secure_1psid=account.psid,
                secure_1psidts=account.psidts,
                proxy=proxy,
                account_index=account.account_index,
                extra_cookies=account.extra_cookies,
            )
            await new_client.init(
                timeout=settings.REQUEST_TIMEOUT_SECONDS,
                auto_close=True,
                close_delay=settings.ACCOUNT_IDLE_CLOSE_SECONDS,
                auto_refresh=True,
                refresh_interval=settings.ACCOUNT_REFRESH_INTERVAL_SECONDS,
            )
            account.client = new_client
            logger.info(f"[registry] init client for account_id={account.id!r}")
            return account.client

    async def close_all(self) -> None:
        """Stop background tasks (`auto_refresh`, `auto_close`) on every active
        client. Called from `_lifespan` exit so graceful shutdown doesn't leave
        asyncio tasks dangling."""
        active = [a.client for a in self._accounts.values() if a.client is not None]
        if not active:
            return
        results = await asyncio.gather(
            *(c.close() for c in active), return_exceptions=True,
        )
        for c, r in zip(active, results, strict=False):
            if isinstance(r, Exception):
                logger.warning(f"[registry] close failed: {r!r} (client={c!r})")
