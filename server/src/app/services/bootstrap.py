"""Boot-time helpers for the in-process AccountRegistry: env/config resolvers
+ `bootstrap_*` routines + Gem URL parser. No runtime state — runtime lives
on `app.state.account_registry` (single-worker uvicorn mandatory)."""
import re

from app import settings
from app.config import CONFIG
from app.logger import logger
from app.services.account_discovery import discover_accounts
from app.services.account_registry import Account, AccountRegistry
from app.utils.browser import get_all_cookie_pairs

_GEM_URL_RX = re.compile(r"/gem/([a-zA-Z0-9_-]+)")


def parse_gem_id(gem_id_or_url: str | None) -> str | None:
    """Accepts a raw Gem id or a full gemini.google.com URL; returns the id
    or None if input is empty."""
    raw = (gem_id_or_url or "").strip()
    if not raw:
        return None
    m = _GEM_URL_RX.search(raw)
    return m.group(1) if m else raw


def _resolve_cookies() -> tuple[str | None, str | None]:
    """Precedence: env > config.ini. Browser cookies are the registry's job
    (`bootstrap_browser_accounts`); pulling them in here would create a duplicate
    `env:0` shadowing the matching `<browser>:N` entry."""
    psid = settings.cookie_1psid_env() or CONFIG["Cookies"].get("gemini_cookie_1psid")
    psidts = settings.cookie_1psidts_env() or CONFIG["Cookies"].get("gemini_cookie_1psidts")
    return (psid or None), (psidts or None)


def _resolve_account_index() -> int:
    """Precedence: env > config.ini > 0. The /u/N for the env-bootstrap account."""
    env_idx = settings.account_index_env()
    if env_idx is not None:
        return env_idx
    raw = CONFIG["Cookies"].get("gemini_account_index") or "0"
    try:
        return int(raw)
    except ValueError:
        return 0


def _resolve_initial_gem_id() -> str | None:
    """Pre-selected Gem for the env-bootstrap account. Precedence: env > config."""
    if v := settings.initial_gem_id_env():
        return v
    if "Gemini" in CONFIG and (v := CONFIG["Gemini"].get("gem_id", "").strip()):
        return v
    return None


def bootstrap_env_account(registry: AccountRegistry) -> None:
    """Upsert `env:0` from env / config.ini cookies. No-op if absent."""
    psid, psidts = _resolve_cookies()
    if not (psid and psidts):
        return
    registry.upsert(
        Account(
            id="env:0",
            source="env",
            psid=psid,
            psidts=psidts,
            account_index=_resolve_account_index(),
            selected_gem_id=_resolve_initial_gem_id(),
        )
    )
    logger.info("Registry: upserted 'env:0' from environment / config.ini")


async def bootstrap_browser_accounts(registry: AccountRegistry) -> int:
    """Run the cross-browser discovery and upsert every account it finds as
    `<browser>:<index>`. Returns the count upserted. Closes any orphan client
    whose cookies just rotated."""
    accounts = await discover_accounts()
    if not accounts:
        return 0
    # `discover_accounts` returns metadata only — pair it back with cookies.
    pairs = get_all_cookie_pairs("gemini")
    upserted = 0
    for a in accounts:
        pair = pairs.get(a["browser"])
        if not pair:
            continue
        psid, psidts = pair
        orphan = registry.upsert(
            Account(
                id=a["id"],
                source="browser",
                psid=psid,
                psidts=psidts,
                account_index=a["index"],
                email=a["email"],
            )
        )
        if orphan and orphan.client is not None:
            try:
                await orphan.client.close()
            except Exception as e:
                logger.warning(f"Failed to close rotated client for {a['id']!r}: {e!r}")
        upserted += 1
        logger.info(f"Registry: upserted {a['id']!r} ({a.get('email', 'no-email')})")
    return upserted


async def bootstrap_registry(registry: AccountRegistry) -> None:
    """Full boot: env account + browser discovery. Nothing to pin (routing is
    explicit per request)."""
    bootstrap_env_account(registry)
    await bootstrap_browser_accounts(registry)
