import re
from typing import ClassVar

import httpx
from app import settings
from app.guards import extension_only
from app.logger import logger
from app.schemas.request import err_response
from app.services.gemini_client import (
    get_selected_gem_id,
    refresh_gemini_client,
    set_selected_gem_id,
)
from litestar import Controller, get, post
from litestar.exceptions import HTTPException
from pydantic import BaseModel, Field

_FORBIDDEN = err_response("Origin is not chrome-extension:// and X-Extension-Id is missing.")


class GemSelection(BaseModel):
    # Raw Gem ID or full URL https://gemini.google.com/u/N/gem/<id>; None/"" clears.
    gem_id: str | None = None


class CookiesPayload(BaseModel):
    cookies: dict[str, str]
    # Chrome supports up to 8 simultaneous Google profiles → /u/0 … /u/7. Mirrors the
    # range scanned by `_probe_gemini_account` so the API and the probe can't disagree.
    account_index: int = Field(default=0, ge=0, le=7)


class AccountInfo(BaseModel):
    index: int
    email: str


# Modern Gemini Web embeds the user email inside inline JSON as "user@host".
# A loose `\w+@\w+` only catches `googlers@google.com` (which is filtered out),
# so we anchor on the quoted form to land on the real account.
_EMAIL_QUOTED_RX = re.compile(r'"([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})"')


def _is_user_email(email: str) -> bool:
    if email.endswith("@google.com"):
        return False
    if "noreply" in email or "no-reply" in email:
        return False
    return not email.endswith("@gemini.google.com")


async def _probe_gemini_account(client: httpx.AsyncClient, idx: int) -> str | None:
    try:
        r = await client.get(f"https://gemini.google.com/u/{idx}/app", timeout=10.0)
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


class RuntimeController(Controller):
    path = "/runtime"
    guards: ClassVar = [extension_only]
    tags: ClassVar = ["runtime"]

    @get(
        "/status",
        summary="Bridge status — currently selected Gem ID",
        responses={403: _FORBIDDEN},
    )
    async def get_status(self) -> dict:
        return {"gem": {"selected_id": get_selected_gem_id()}}

    @post(
        "/gem",
        status_code=200,
        summary="Select / clear active Gem",
        responses={403: _FORBIDDEN},
    )
    async def select_gem(self, data: GemSelection) -> dict:
        set_selected_gem_id(data.gem_id)
        # Don't interpolate request headers — they are spoofable by any local
        # process and would let a caller plant arbitrary strings into logs.
        logger.info(f"Gem selection updated: {get_selected_gem_id()!r}")
        return {"selected_id": get_selected_gem_id()}


class AuthController(Controller):
    path = "/auth"
    guards: ClassVar = [extension_only]
    tags: ClassVar = ["auth"]

    @post(
        "/cookies/{provider:str}",
        status_code=200,
        summary="Push Google session cookies",
        responses={
            400: err_response("Missing __Secure-1PSID or __Secure-1PSIDTS in body."),
            403: _FORBIDDEN,
            501: err_response("Provider other than 'gemini' is not wired."),
            502: err_response("Authentication failed with the provided cookies."),
        },
    )
    async def update_cookies(self, provider: str, data: CookiesPayload) -> dict:
        if provider != "gemini":
            raise HTTPException(
                status_code=501,
                detail=f"Provider '{provider}' is not wired on the server side yet.",
            )
        psid = data.cookies.get("__Secure-1PSID")
        psidts = data.cookies.get("__Secure-1PSIDTS")
        if not psid or not psidts:
            raise HTTPException(status_code=400, detail="Missing __Secure-1PSID or __Secure-1PSIDTS")
        result = await refresh_gemini_client(
            psid, psidts,
            account_index=data.account_index,
            extra_cookies=data.cookies,
        )
        if result == "failed":
            raise HTTPException(status_code=502, detail="Failed to authenticate with provided cookies")
        if result == "refreshed":
            logger.info(f"Provider 'gemini' refreshed (u/{data.account_index})")
        return {
            "status": "ok",
            "provider": provider,
            "account_index": data.account_index,
            "deduped": result == "deduped",
        }

    @post(
        "/accounts/{provider:str}",
        status_code=200,
        summary="Probe /u/N pages to list authenticated accounts",
        responses={403: _FORBIDDEN, 501: err_response("Provider other than 'gemini' is not wired.")},
    )
    async def list_accounts(self, provider: str, data: CookiesPayload) -> list[AccountInfo]:
        if provider != "gemini":
            raise HTTPException(status_code=501, detail=f"Provider '{provider}' not supported.")
        cookies = dict(data.cookies)
        headers = {"User-Agent": settings.PROBE_USER_AGENT}
        found: list[AccountInfo] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(cookies=cookies, headers=headers, follow_redirects=True) as client:
            for idx in range(8):
                email = await _probe_gemini_account(client, idx)
                if not email:
                    continue
                if email in seen:
                    break
                seen.add(email)
                found.append(AccountInfo(index=idx, email=email))
        return found
