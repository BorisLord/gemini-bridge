# NOTE: single-worker uvicorn only. Module globals (`_gemini_client`,
# `_selected_gem_id`, `_last_refresh_signature`, `_initialization_error`) are
# mutated without locks. Running uvicorn with --workers > 1 will give each
# worker its own copy and break refresh-via-/auth/cookies (the worker that
# served /auth/cookies is not necessarily the one that handles the next
# /v1/chat/completions). `start.sh` always launches a single worker.
import contextlib
import re

from models.gemini import MyGeminiClient
from app import settings
from app.config import CONFIG
from app.logger import logger
from app.utils.browser import get_cookie_from_browser

from gemini_webapi.exceptions import AuthError


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
    if not gem_id_or_url:
        _selected_gem_id = None
        return
    raw = gem_id_or_url.strip()
    if not raw:
        _selected_gem_id = None
        return
    m = _GEM_URL_RX.search(raw)
    _selected_gem_id = m.group(1) if m else raw


def _resolve_cookies() -> tuple[str | None, str | None]:
    """Precedence: env > config.conf > browser_cookie3 fallback."""
    psid = settings.cookie_1psid_env() or CONFIG["Cookies"].get("gemini_cookie_1PSID")
    psidts = settings.cookie_1psidts_env() or CONFIG["Cookies"].get("gemini_cookie_1PSIDTS")
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


async def init_gemini_client() -> bool:
    global _gemini_client, _initialization_error, _selected_gem_id
    _initialization_error = None

    try:
        psid, psidts = _resolve_cookies()
        proxy = CONFIG["Proxy"].get("http_proxy") or None

        if not (psid and psidts):
            _initialization_error = (
                "Gemini cookies not found. Provide them via the Chrome extension, "
                "GEMINI_COOKIE_1PSID/_1PSIDTS env vars, [Cookies] in config.conf, "
                "or ensure your browser is logged in."
            )
            logger.warning(_initialization_error)
            return False

        _gemini_client = MyGeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=proxy,
            account_index=_resolve_account_index(),
        )
        await _gemini_client.init()

        if _selected_gem_id is None:
            _selected_gem_id = _resolve_initial_gem_id()
            if _selected_gem_id:
                logger.info(f"Pre-selected Gem from config: {_selected_gem_id!r}")

        logger.info("Gemini client initialized successfully.")
        return True

    except AuthError as e:
        _initialization_error = f"Gemini authentication failed: {e} (cookies expired/invalid)"
        logger.error(_initialization_error)
        _gemini_client = None
        return False
    except Exception as e:
        _initialization_error = f"Unexpected error initializing Gemini client: {e}"
        logger.error(_initialization_error, exc_info=True)
        _gemini_client = None
        return False


async def refresh_gemini_client(
    psid: str,
    psidts: str,
    account_index: int = 0,
    extra_cookies: dict | None = None,
) -> str:
    """Hot-rotate auth without restarting. Returns "refreshed" / "deduped" / "failed"."""
    global _gemini_client, _initialization_error, _last_refresh_signature
    # Include the full extras dict in the signature so a rotated SAPISID forces
    # a real refresh even if 1PSID didn't change.
    extras_sig = tuple(sorted((extra_cookies or {}).items()))
    sig = (psid, psidts, account_index, extras_sig)
    if _last_refresh_signature == sig and _gemini_client is not None:
        return "deduped"

    proxy = CONFIG["Proxy"].get("http_proxy") or None
    try:
        new_client = MyGeminiClient(
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


def get_gemini_client():
    if _gemini_client is None:
        error_detail = _initialization_error or "Gemini client was not initialized. Check logs for details."
        raise GeminiClientNotInitializedError(error_detail)
    return _gemini_client

