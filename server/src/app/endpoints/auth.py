import re
import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app import settings
from app.services.gemini_client import (
    refresh_gemini_client,
    get_selected_gem_id,
    set_selected_gem_id,
)
from app.logger import logger

router = APIRouter(prefix="/auth", tags=["auth"])
status_router = APIRouter(prefix="/admin", tags=["admin"])


@status_router.get("/status")
async def get_status(
    origin: str | None = Header(default=None),
    x_extension_id: str | None = Header(default=None),
):
    _check_extension(origin, x_extension_id)
    return {
        "gem": {"selected_id": get_selected_gem_id()},
    }


class GemSelection(BaseModel):
    # Raw Gem ID or full URL https://gemini.google.com/u/N/gem/<id>; None/"" clears.
    gem_id: str | None = None


@status_router.post("/gem")
async def select_gem(
    payload: GemSelection,
    origin: str | None = Header(default=None),
    x_extension_id: str | None = Header(default=None),
):
    _check_extension(origin, x_extension_id)
    set_selected_gem_id(payload.gem_id)
    # Don't interpolate `origin`/`x_extension_id` — both are spoofable by any
    # local process and would let a caller plant arbitrary strings into logs.
    logger.info(f"Gem selection updated: {get_selected_gem_id()!r}")
    return {"selected_id": get_selected_gem_id()}


class CookiesPayload(BaseModel):
    cookies: dict[str, str]
    account_index: int = Field(default=0, ge=0, le=20)


class AccountInfo(BaseModel):
    index: int
    email: str


def _check_extension(
    origin: str | None = None,
    x_extension_id: str | None = None,
) -> None:
    """Accept iff Origin=chrome-extension://… OR X-Extension-Id is set. The latter
    covers GETs where Chrome strips Origin (host_permissions, same-origin-like).
    CSRF/inter-extension hygiene — not real authn (both signals are spoofable)."""
    if origin and origin.startswith("chrome-extension://"):
        return
    if x_extension_id:
        return
    raise HTTPException(
        403,
        "Origin must be chrome-extension:// or request must carry X-Extension-Id header.",
    )


_EMAIL_RX = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


async def _probe_gemini_account(client: httpx.AsyncClient, idx: int) -> str | None:
    try:
        r = await client.get(f"https://gemini.google.com/u/{idx}/app", timeout=10.0)
    except Exception as e:
        logger.debug(f"Account probe u/{idx} failed: {e}")
        return None
    if r.status_code != 200:
        return None
    # /u/{idx} redirected to /u/0 means the index is out of range.
    final_path = str(r.url.path)
    if idx > 0 and (final_path.startswith("/u/0") or final_path == "/app"):
        return None
    for email in _EMAIL_RX.findall(r.text[:300000]):
        if email.endswith("@google.com"):
            continue
        if "noreply" in email or "no-reply" in email:
            continue
        if email.endswith("@gemini.google.com"):
            continue
        return email
    return None


@router.post("/cookies/{provider}")
async def update_cookies(
    provider: str,
    payload: CookiesPayload,
    origin: str | None = Header(default=None),
    x_extension_id: str | None = Header(default=None),
):
    _check_extension(origin, x_extension_id)
    if provider == "gemini":
        psid = payload.cookies.get("__Secure-1PSID")
        psidts = payload.cookies.get("__Secure-1PSIDTS")
        if not psid or not psidts:
            raise HTTPException(400, "Missing __Secure-1PSID or __Secure-1PSIDTS")
        result = await refresh_gemini_client(
            psid, psidts,
            account_index=payload.account_index,
            extra_cookies=payload.cookies,
        )
        if result == "failed":
            raise HTTPException(502, "Failed to authenticate with provided cookies")
        if result == "refreshed":
            logger.info(f"Provider 'gemini' refreshed (u/{payload.account_index})")
        return {
            "status": "ok",
            "provider": provider,
            "account_index": payload.account_index,
            "deduped": result == "deduped",
        }
    raise HTTPException(501, f"Provider '{provider}' is not wired on the server side yet.")


@router.post("/accounts/{provider}", response_model=list[AccountInfo])
async def list_accounts(
    provider: str,
    payload: CookiesPayload,
    origin: str | None = Header(default=None),
    x_extension_id: str | None = Header(default=None),
):
    _check_extension(origin, x_extension_id)
    if provider != "gemini":
        raise HTTPException(501, f"Provider '{provider}' not supported.")
    cookies = dict(payload.cookies)
    headers = {"User-Agent": settings.PROBE_USER_AGENT}
    found: list[AccountInfo] = []
    seen: set = set()
    async with httpx.AsyncClient(cookies=cookies, headers=headers, follow_redirects=True) as client:
        for idx in range(8):
            email = await _probe_gemini_account(client, idx)
            if not email:
                continue
            if email in seen:
                # Same email twice = /u/N wrapped around (idx out of range).
                break
            seen.add(email)
            found.append(AccountInfo(index=idx, email=email))
    return found
