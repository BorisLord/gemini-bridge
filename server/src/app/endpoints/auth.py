from typing import ClassVar

import httpx
from app import settings
from app.endpoints._shared import FORBIDDEN, registry
from app.guards import extension_only
from app.logger import logger
from app.schemas.openai_chat import err_response
from app.services.account_discovery import probe_gemini_account
from app.services.account_registry import Account
from litestar import Controller, Request, post
from litestar.exceptions import HTTPException
from pydantic import BaseModel, Field


class CookiesPayload(BaseModel):
    cookies: dict[str, str]
    # Chrome supports up to 8 simultaneous Google profiles → /u/0 … /u/7. Mirrors the
    # range scanned by `probe_gemini_account` so the API and the probe can't disagree.
    account_index: int = Field(default=0, ge=0, le=7)
    # Optional — the extension caches the index→email map from
    # `/auth/accounts/gemini` and forwards it so `/accounts/` shows it without
    # a re-probe (which would fail anyway on Chrome device-bound cookies).
    email: str | None = None


class AccountInfo(BaseModel):
    index: int
    email: str


class AuthController(Controller):
    path = "/auth"
    guards: ClassVar = [extension_only]
    tags: ClassVar = ["auth"]

    @post(
        "/cookies/{provider:str}",
        status_code=200,
        summary="Push Google session cookies (upserts extension:<account_index>)",
        responses={
            400: err_response("Missing __Secure-1PSID or __Secure-1PSIDTS in body."),
            403: FORBIDDEN,
            501: err_response("Provider other than 'gemini' is not wired."),
        },
    )
    async def update_cookies(self, provider: str, data: CookiesPayload, request: Request) -> dict:
        if provider != "gemini":
            raise HTTPException(
                status_code=501,
                detail=f"Provider '{provider}' is not wired on the server side yet.",
            )
        psid = data.cookies.get("__Secure-1PSID")
        psidts = data.cookies.get("__Secure-1PSIDTS")
        if not psid or not psidts:
            raise HTTPException(status_code=400, detail="Missing __Secure-1PSID or __Secure-1PSIDTS")
        reg = registry(request)
        account_id = f"extension:{data.account_index}"
        existing = reg.get(account_id)
        new = Account(
            id=account_id,
            source="extension",
            psid=psid,
            psidts=psidts,
            account_index=data.account_index,
            email=data.email,
            extra_cookies=dict(data.cookies),
        )
        orphan = reg.upsert(new)
        # Cookies rotated: close the prior client so its auto_refresh /
        # auto_close background tasks don't keep running on stale credentials.
        if orphan and orphan.client is not None:
            try:
                await orphan.client.close()
            except Exception as e:
                logger.warning(f"Failed to close rotated client for {account_id!r}: {e!r}")
        logger.info(
            f"Provider 'gemini' upserted as {account_id!r} "
            f"({'replaced' if existing else 'created'})"
        )
        return {
            "status": "ok",
            "provider": provider,
            "account_id": account_id,
            "account_index": data.account_index,
            "replaced": existing is not None,
        }

    @post(
        "/accounts/{provider:str}",
        status_code=200,
        summary="Probe /u/N pages to list authenticated accounts",
        responses={403: FORBIDDEN, 501: err_response("Provider other than 'gemini' is not wired.")},
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
                email = await probe_gemini_account(client, idx)
                if not email:
                    continue
                if email in seen:
                    break
                seen.add(email)
                found.append(AccountInfo(index=idx, email=email))
        return found
