"""Cross-browser, cross-`/u/N/` Gemini account discovery.

For every browser session with a complete `(__Secure-1PSID, __Secure-1PSIDTS)`
pair, probe `/u/0..7/app` and scrape the embedded email. Returned ids are
stable across runs as `<browser>:<account_index>` — the routing key the rest
of the bridge uses."""
import asyncio
import re

import httpx
from app import settings
from app.logger import logger
from app.utils.browser import get_all_cookie_pairs

GEMINI_HOST = "gemini.google.com"

# Modern Gemini Web embeds the user email inside inline JSON as "user@host".
# A loose `\w+@\w+` only catches `googlers@google.com` (which is filtered out),
# so we anchor on the quoted form to land on the real account.
_EMAIL_QUOTED_RX = re.compile(r'"([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})"')


def parse_account_id(account_id: str) -> tuple[str, int] | None:
    """Parse a `<browser>:<account_index>` selector into `(browser, index)`.
    Returns None if malformed or out of the 0..7 /u/N range Google supports."""
    if not isinstance(account_id, str):
        return None
    try:
        browser, idx_str = account_id.split(":", 1)
        idx = int(idx_str)
    except ValueError:
        return None
    if not browser or not 0 <= idx <= 7:
        return None
    return browser, idx


def resolve_session_for_account_id(account_id: str) -> tuple[str, str, int] | None:
    """Returns `(psid, psidts, account_index)` for a discovered account id,
    or None if the browser session no longer has cookies (user signed out)."""
    parsed = parse_account_id(account_id)
    if not parsed:
        return None
    browser, idx = parsed
    pairs = get_all_cookie_pairs("gemini")
    pair = pairs.get(browser)
    if not pair:
        return None
    return pair[0], pair[1], idx


def _is_user_email(email: str) -> bool:
    """Filter out service emails Gemini Web embeds in the page (googlers,
    no-reply addresses, the gemini.google.com host itself)."""
    if email.endswith("@google.com"):
        return False
    if "noreply" in email or "no-reply" in email:
        return False
    return not email.endswith("@gemini.google.com")


async def probe_gemini_account(client: httpx.AsyncClient, idx: int) -> str | None:
    """Returns the user email for `/u/{idx}/app`, or None if the slot is empty
    (Google redirects unused slots to /u/0). Shared by `discover_accounts` and
    `/auth/accounts/{provider}`."""
    try:
        r = await client.get(f"https://{GEMINI_HOST}/u/{idx}/app", timeout=10.0)
    except Exception as e:
        logger.debug(f"Account probe u/{idx} failed: {e}")
        return None
    if r.status_code != 200:
        return None
    final_path = str(r.url.path)
    if idx > 0 and (final_path.startswith("/u/0") or final_path == "/app"):
        return None
    for email in _EMAIL_QUOTED_RX.findall(r.text[:600000]):
        if _is_user_email(email):
            return email
    return None


async def _discover_browser(browser: str, psid: str, psidts: str) -> list[dict]:
    """Probe /u/0..7 for one browser session, return its accounts as
    `[{id, browser, index, email}]` (empty if none authenticated)."""
    cookies = {"__Secure-1PSID": psid, "__Secure-1PSIDTS": psidts}
    headers = {"User-Agent": settings.PROBE_USER_AGENT}
    # Cap concurrent connections per session — Google rate-limits /u/N probes
    # and N*8 unbounded GETs (N browsers in parallel) is a fast path to a
    # captcha wall (`/sorry/index`, see Known pitfalls).
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
    found: list[dict] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        cookies=cookies, headers=headers, follow_redirects=True, limits=limits,
    ) as client:
        for idx in range(8):
            email = await probe_gemini_account(client, idx)
            if not email:
                continue
            # Repeated email = we've fallen off the chained-account list (Google
            # serves the same /u/0 page for unused slots).
            if email in seen:
                break
            seen.add(email)
            found.append({
                "id": f"{browser}:{idx}",
                "browser": browser,
                "index": idx,
                "email": email,
            })
    return found


async def discover_accounts() -> list[dict]:
    """Returns `[{id, browser, index, email}]` for every reachable account.
    Browsers in parallel, /u/N scan sequential per browser (Google rate-limits
    per-session). Per-browser failures are logged and skipped."""
    pairs = get_all_cookie_pairs("gemini")
    if not pairs:
        return []
    browsers = list(pairs.keys())
    results = await asyncio.gather(
        *(_discover_browser(browser, psid, psidts) for browser, (psid, psidts) in pairs.items()),
        return_exceptions=True,
    )
    flat: list[dict] = []
    for browser, outcome in zip(browsers, results, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning(f"Account discovery failed for {browser!r}: {outcome}")
            continue
        flat.extend(outcome)
    return flat
