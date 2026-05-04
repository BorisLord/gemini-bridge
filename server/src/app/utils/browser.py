"""Local-browser cookie fallback. Reached only when the extension isn't loaded
and `GEMINI_COOKIE_*` / `config.conf` are empty. Delegates browser discovery
to `gemini_webapi.utils.load_browser_cookies` which queries every supported
browser in parallel (chrome, chromium, opera, opera_gx, brave, edge, vivaldi,
firefox, librewolf, safari) and silently skips ones that fail / aren't
installed. `[Browser].name` in `config.conf` is honored as a *preference*:
when set and present in the result, that browser's cookies win the tie."""
from typing import Literal

from app.config import CONFIG
from app.logger import logger
from gemini_webapi.utils import load_browser_cookies


def _extract_pair(cookies: list[dict]) -> tuple[str, str] | None:
    psid = next((c["value"] for c in cookies if c["name"] == "__Secure-1PSID"), None)
    psidts = next((c["value"] for c in cookies if c["name"] == "__Secure-1PSIDTS"), None)
    if not (psid and psidts):
        return None
    if not psid.strip() or not psidts.strip():
        return None
    return psid, psidts


def get_all_cookie_pairs(service: Literal["gemini"]) -> dict[str, tuple[str, str]]:
    """Returns every browser that has a complete `(__Secure-1PSID, __Secure-1PSIDTS)`
    pair for `google.com`, as `{browser_name: (psid, psidts)}`.

    Foundation for the multi-account discovery flow: each browser session is a
    distinct Google login, and within each session `/u/N/` indexes the accounts
    chained on that login. Caller iterates this dict and probes per-session."""
    if service != "gemini":
        logger.warning(f"Unsupported service: {service}")
        return {}

    by_browser = load_browser_cookies(domain_name="google.com")
    if not by_browser:
        return {}

    pairs: dict[str, tuple[str, str]] = {}
    for browser, cookies in by_browser.items():
        pair = _extract_pair(cookies)
        if pair:
            pairs[browser] = pair
    return pairs


def get_cookie_from_browser(service: Literal["gemini"]) -> tuple[str, str] | None:
    """Returns `(__Secure-1PSID, __Secure-1PSIDTS)` from the first browser that
    has both. The preferred browser (`[Browser].name`) is tried first; on miss
    we walk every other browser the lib found cookies in. Returns None only if
    no browser has a complete pair.

    Used at boot when the bridge needs *one* session to start serving without
    config — see `get_all_cookie_pairs` for the discovery path that surfaces
    every available session."""
    if service != "gemini":
        logger.warning(f"Unsupported service: {service}")
        return None

    pairs = get_all_cookie_pairs(service)
    if not pairs:
        logger.warning("No browser cookies found (browser-cookie3 missing or no installed browser).")
        return None

    preferred = CONFIG["Browser"].get("name", "").lower()
    if preferred and preferred in pairs:
        logger.info(f"Loaded Gemini cookies from local {preferred} profile (preferred).")
        return pairs[preferred]
    browser, pair = next(iter(pairs.items()))
    logger.info(f"Loaded Gemini cookies from local {browser} profile (auto-picked).")
    return pair
