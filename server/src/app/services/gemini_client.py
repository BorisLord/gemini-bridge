# NOTE: single-worker uvicorn only. Module globals (`_gemini_client`,
# `_selected_gem_id`, `_last_refresh_signature`, `_initialization_error`) are
# mutated without locks. Running uvicorn with --workers > 1 will give each
# worker its own copy and break refresh-via-/auth/cookies (the worker that
# served /auth/cookies is not necessarily the one that handles the next
# /v1/chat/completions). `start.sh` always launches a single worker.
import configparser
import contextlib
import re
from pathlib import Path
from typing import Literal

from app import settings
from app.config import CONFIG
from app.logger import logger
from app.services.account_discovery import parse_account_id, resolve_session_for_account_id
from app.services.gemini_wrapper import BridgeGeminiClient
from app.utils.browser import get_cookie_from_browser
from gemini_webapi.exceptions import AuthError

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.conf"

RefreshResult = Literal["refreshed", "deduped", "failed"]
# Boot-time outcomes — distinct so the lifespan can log them differently.
# - "ok"          : client up, ready to serve
# - "no-cookies"  : soft, expected on fresh installs (extension will push them)
# - "auth-failed" : cookies provided but Google rejected them (user-actionable)
# - "error"       : unexpected exception during init (bug or upstream outage)
InitResult = Literal["ok", "no-cookies", "auth-failed", "error"]


class GeminiClientNotInitializedError(Exception):
    pass


_gemini_client = None
_initialization_error = None

# We don't fetch/cache the Gem catalogue — Google's LIST_GEMS RPC returns
# PERMISSION_DENIED on many accounts. The user pastes a Gem URL/ID instead.
_selected_gem_id: str | None = None

# The Chrome extension fires `chrome.cookies.onChanged` for every cookie in
# `cookieNames` (8+) on each Google refresh — 5+ identical pushes/sec. Dedup
# on signature avoids triggering a fresh `client.init()` for each.
_last_refresh_signature: tuple | None = None


def get_selected_gem_id() -> str | None:
    return _selected_gem_id


_GEM_URL_RX = re.compile(r"/gem/([a-zA-Z0-9_-]+)")


def set_selected_gem_id(gem_id_or_url: str | None) -> None:
    """Accepts a raw ID or a full gemini.google.com URL; empty/None clears."""
    global _selected_gem_id
    raw = (gem_id_or_url or "").strip()
    if not raw:
        _selected_gem_id = None
        return
    m = _GEM_URL_RX.search(raw)
    _selected_gem_id = m.group(1) if m else raw


def _resolve_cookies() -> tuple[str | None, str | None]:
    """Precedence: env > config.conf > browser_cookie3 fallback."""
    psid = settings.cookie_1psid_env() or CONFIG["Cookies"].get("gemini_cookie_1psid")
    psidts = settings.cookie_1psidts_env() or CONFIG["Cookies"].get("gemini_cookie_1psidts")
    if not psid or not psidts:
        from_browser = get_cookie_from_browser("gemini")
        if from_browser:
            psid, psidts = from_browser
    return (psid or None), (psidts or None)


def _resolve_account_index() -> int:
    """Precedence: env > config.conf > 0."""
    env_idx = settings.account_index_env()
    if env_idx is not None:
        return env_idx
    raw = CONFIG["Cookies"].get("gemini_account_index") or "0"
    try:
        return int(raw)
    except ValueError:
        return 0


def _resolve_initial_gem_id() -> str | None:
    """Precedence: env > config.conf > None."""
    if v := settings.initial_gem_id_env():
        return v
    if "Gemini" in CONFIG and (v := CONFIG["Gemini"].get("gem_id", "").strip()):
        return v
    return None


def _resolve_selected_account_id() -> str | None:
    """The cross-browser selector persisted by `POST /accounts/use`. When set,
    it overrides `[Cookies].gemini_cookie_*` because the user has explicitly
    pinned a (browser, /u/N) pair — we re-discover its current cookies on
    every boot so a rotated SAPISID is picked up automatically.

    Precedence: env > config.conf."""
    return settings.selected_account_id_env() or CONFIG["Cookies"].get("selected_account_id") or None


def persist_selected_account_id(account_id: str | None) -> None:
    """Write the cross-browser selector to `config.conf` (None clears).
    Creates `config.conf` if missing so a Docker fresh-install survives the
    very first `POST /accounts/use`. `chmod 0o600` because the selector reveals
    which Google account is bound to this bridge."""
    if account_id is None:
        CONFIG["Cookies"].pop("selected_account_id", None)
    else:
        CONFIG["Cookies"]["selected_account_id"] = account_id

    cfg = configparser.ConfigParser()
    if _CONFIG_PATH.exists():
        cfg.read(_CONFIG_PATH, encoding="utf-8")
    else:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if "Cookies" not in cfg:
        cfg["Cookies"] = {}
    if account_id is None:
        cfg["Cookies"].pop("selected_account_id", None)
    else:
        cfg["Cookies"]["selected_account_id"] = account_id

    tmp = _CONFIG_PATH.with_suffix(_CONFIG_PATH.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        cfg.write(f)
    tmp.chmod(0o600)
    tmp.replace(_CONFIG_PATH)


async def init_gemini_client() -> InitResult:
    global _gemini_client, _initialization_error, _selected_gem_id
    _initialization_error = None

    try:
        proxy = CONFIG["Proxy"].get("http_proxy") or None

        # `selected_account_id` (e.g. "firefox:1") trumps cached cookies — the
        # user picked a specific session and we must read it fresh from disk.
        # Fall back to the legacy precedence only if discovery fails.
        psid: str | None = None
        psidts: str | None = None
        account_index = 0
        if selected_id := _resolve_selected_account_id():
            if parse_account_id(selected_id) is None:
                logger.warning(
                    f"selected_account_id={selected_id!r} is malformed — "
                    f"expected `<browser>:<index>` with index in 0..7. Ignored."
                )
            else:
                resolved = resolve_session_for_account_id(selected_id)
                if resolved:
                    psid, psidts, account_index = resolved
                    logger.info(f"Boot using selected_account_id={selected_id!r}")
                else:
                    logger.warning(
                        f"selected_account_id={selected_id!r} no longer resolvable "
                        f"(browser session gone?) — falling back to cached cookies."
                    )
        if not (psid and psidts):
            psid, psidts = _resolve_cookies()
            account_index = _resolve_account_index()

        if not (psid and psidts):
            _initialization_error = (
                "Gemini cookies not found. Provide them via the Chrome extension, "
                "GEMINI_COOKIE_1PSID/_1PSIDTS env vars, [Cookies] in config.conf, "
                "or ensure your browser is logged in."
            )
            logger.warning(_initialization_error)
            return "no-cookies"

        _gemini_client = BridgeGeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=proxy,
            account_index=account_index,
        )
        await _gemini_client.init()

        if _selected_gem_id is None:
            _selected_gem_id = _resolve_initial_gem_id()
            if _selected_gem_id:
                logger.info(f"Pre-selected Gem from config: {_selected_gem_id!r}")

        logger.info("Gemini client initialized successfully.")
        return "ok"

    except AuthError as e:
        _initialization_error = f"Gemini authentication failed: {e} (cookies expired/invalid)"
        logger.error(_initialization_error)
        _gemini_client = None
        return "auth-failed"
    except Exception as e:
        _initialization_error = f"Unexpected error initializing Gemini client: {e}"
        logger.error(_initialization_error, exc_info=True)
        _gemini_client = None
        return "error"


async def refresh_gemini_client(
    psid: str,
    psidts: str,
    account_index: int = 0,
    extra_cookies: dict | None = None,
) -> RefreshResult:
    """Hot-rotate auth without restarting."""
    global _gemini_client, _initialization_error, _last_refresh_signature
    # Include the full extras dict in the signature so a rotated SAPISID forces
    # a real refresh even if 1PSID didn't change.
    extras_sig = tuple(sorted((extra_cookies or {}).items()))
    sig = (psid, psidts, account_index, extras_sig)
    if _last_refresh_signature == sig and _gemini_client is not None:
        return "deduped"

    proxy = CONFIG["Proxy"].get("http_proxy") or None
    try:
        new_client = BridgeGeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=proxy,
            account_index=account_index,
            extra_cookies=extra_cookies,
        )
        await new_client.init()
    except Exception as e:
        logger.error(f"Failed to refresh Gemini client: {e}")
        _initialization_error = str(e)
        _last_refresh_signature = None
        return "failed"
    old_client = _gemini_client
    _gemini_client = new_client
    _initialization_error = None
    _last_refresh_signature = sig
    CONFIG["Cookies"]["gemini_cookie_1psid"] = psid
    CONFIG["Cookies"]["gemini_cookie_1psidts"] = psidts
    CONFIG["Cookies"]["gemini_account_index"] = str(account_index)
    if old_client is not None:
        with contextlib.suppress(Exception):
            await old_client.close()
    logger.info(f"Gemini client refreshed (account_index={account_index}).")
    return "refreshed"


def get_gemini_client() -> BridgeGeminiClient:
    if _gemini_client is None:
        error_detail = _initialization_error or "Gemini client was not initialized. Check logs for details."
        raise GeminiClientNotInitializedError(error_detail)
    return _gemini_client

