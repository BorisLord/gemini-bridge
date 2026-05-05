"""Local-browser cookie reader. Delegates to `gemini_webapi.utils.load_browser_cookies`
(chrome, chromium, opera, opera_gx, brave, edge, vivaldi, firefox, librewolf,
safari — silently skips unavailable ones). `[Browser].name` in `config.ini`
is honored as a tie-break preference for the single-pair path."""
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
    """Returns `{browser_name: (psid, psidts)}` for every browser with a
    complete cookie pair on `google.com`. Each entry is a distinct Google
    login; `/u/N/` then indexes the accounts chained inside that login."""
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
    """Single-pair lookup: preferred browser first (`[Browser].name`), else any
    available. Returns None if no browser holds a complete pair."""
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
