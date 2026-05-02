"""Local-browser cookie fallback. Reached only when the extension isn't
loaded and `GEMINI_COOKIE_*` / `config.conf` are empty. Linux/macOS only —
Windows users go through WSL or paste cookies."""
import logging
from typing import Literal
from collections.abc import Callable

import browser_cookie3

from app.config import CONFIG

logger = logging.getLogger(__name__)


_LOADERS: dict[str, Callable] = {
    "firefox": browser_cookie3.firefox,
    "librewolf": browser_cookie3.librewolf,
    "chrome": browser_cookie3.chrome,
    "chromium": browser_cookie3.chromium,
    "brave": browser_cookie3.brave,
    "edge": browser_cookie3.edge,
    "opera": browser_cookie3.opera,
    "opera_gx": browser_cookie3.opera_gx,
    "vivaldi": browser_cookie3.vivaldi,
}


def get_cookie_from_browser(service: Literal["gemini"]) -> tuple[str, str] | None:
    """Returns `(__Secure-1PSID, __Secure-1PSIDTS)` iff both are present and non-empty."""
    if service != "gemini":
        logger.warning(f"Unsupported service: {service}")
        return None

    browser_name = CONFIG["Browser"].get("name", "firefox").lower()
    loader = _LOADERS.get(browser_name)
    if loader is None:
        logger.warning(
            f"Unsupported browser {browser_name!r} in [Browser].name. "
            f"Pick one of: {sorted(_LOADERS)}"
        )
        return None

    logger.info(f"Looking up Gemini cookies in local {browser_name} profile.")
    try:
        jar = loader()
    except Exception as e:
        logger.warning(f"browser_cookie3.{browser_name} failed: {e}")
        return None

    psid = psidts = None
    for cookie in jar:
        if "google" not in (cookie.domain or ""):
            continue
        if cookie.name == "__Secure-1PSID":
            psid = cookie.value
        elif cookie.name == "__Secure-1PSIDTS":
            psidts = cookie.value

    if not (psid and psidts):
        logger.warning("Gemini cookies not found or incomplete in local browser.")
        return None
    if not psid.strip() or not psidts.strip():
        logger.warning("Gemini cookies present but empty.")
        return None

    return psid, psidts
