import re
import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, List

from app.services.gemini_client import refresh_gemini_client
from app.services.mode_control import request_mode, VALID_MODES
from app.services.fallback import get_last_fallback_event, FALLBACK_MODEL, reset_sticky
from app.logger import logger

router = APIRouter(prefix="/auth", tags=["auth"])
status_router = APIRouter(prefix="/admin", tags=["admin"])


@status_router.get("/status")
async def get_status():
    """Read-only status: which mode (webai/g4f) and whether g4f is installed."""
    try:
        import g4f  # noqa: F401
        g4f_installed = True
    except ImportError:
        g4f_installed = False
    return {
        "mode": "webai",  # FastAPI worker only runs in webai mode; g4f mode replaces this process entirely
        "g4f_installed": g4f_installed,
        "fallback_model": FALLBACK_MODEL,
        "last_fallback": get_last_fallback_event(),
        "switch_hint": "POST /admin/mode {\"mode\":\"g4f\"} to switch (also '1'/'2' on stdin in native foreground).",
    }


class ModeRequest(BaseModel):
    mode: str


@status_router.post("/reset-fallback")
async def reset_fallback(origin: Optional[str] = Header(default=None)):
    """Clear the sticky fallback window — next request will retry Gemini."""
    _check_origin(origin)
    reset_sticky()
    return {"status": "ok", "sticky_until": None}


@status_router.post("/mode")
async def set_mode(payload: ModeRequest, origin: Optional[str] = Header(default=None)):
    """Request a mode switch. The supervisor (run.py) picks it up within ~1s."""
    _check_origin(origin)
    if payload.mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {VALID_MODES}")
    if payload.mode == "g4f":
        try:
            import g4f  # noqa: F401
        except ImportError:
            raise HTTPException(
                400,
                "g4f is not installed. Rebuild with WITH_G4F=1 (Docker: "
                "`WITH_G4F=1 docker compose build && docker compose up -d`; "
                "native: `WITH_G4F=1 ./start.sh`).",
            )
    request_mode(payload.mode)
    logger.info(f"Mode switch requested: {payload.mode} (by {origin})")
    return {"status": "switching", "mode": payload.mode}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class CookiesPayload(BaseModel):
    cookies: Dict[str, str]
    account_index: int = Field(default=0, ge=0, le=20)


class AccountInfo(BaseModel):
    index: int
    email: str


def _check_origin(origin: Optional[str]) -> None:
    if not origin or not origin.startswith("chrome-extension://"):
        raise HTTPException(403, "Origin must be a chrome-extension:// URL")


_EMAIL_RX = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


async def _probe_gemini_account(client: httpx.AsyncClient, idx: int) -> Optional[str]:
    """Hit gemini.google.com/u/{idx}/app and extract the signed-in email."""
    try:
        r = await client.get(f"https://gemini.google.com/u/{idx}/app", timeout=10.0)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    # If Google redirected /u/{idx} to /u/{0} the index is out of range.
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
    origin: Optional[str] = Header(default=None),
):
    _check_origin(origin)
    if provider == "gemini":
        psid = payload.cookies.get("__Secure-1PSID")
        psidts = payload.cookies.get("__Secure-1PSIDTS")
        if not psid or not psidts:
            raise HTTPException(400, "Missing __Secure-1PSID or __Secure-1PSIDTS")
        ok = await refresh_gemini_client(psid, psidts, account_index=payload.account_index)
        if not ok:
            raise HTTPException(502, "Failed to authenticate with provided cookies")
        logger.info(f"Provider 'gemini' refreshed (u/{payload.account_index}) by {origin}")
        return {"status": "ok", "provider": provider, "account_index": payload.account_index}
    raise HTTPException(501, f"Provider '{provider}' is not wired on the server side yet.")


@router.post("/accounts/{provider}", response_model=List[AccountInfo])
async def list_accounts(
    provider: str,
    payload: CookiesPayload,
    origin: Optional[str] = Header(default=None),
):
    """Probe /u/0…/u/MAX with the provided cookies and return discovered accounts."""
    _check_origin(origin)
    if provider != "gemini":
        raise HTTPException(501, f"Provider '{provider}' not supported.")
    cookies = {k: v for k, v in payload.cookies.items()}
    headers = {"User-Agent": UA}
    found: List[AccountInfo] = []
    seen: set = set()
    async with httpx.AsyncClient(cookies=cookies, headers=headers, follow_redirects=True) as client:
        for idx in range(0, 8):
            email = await _probe_gemini_account(client, idx)
            if not email:
                continue
            if email in seen:
                # If two indices yield the same email, /u/N has wrapped around (idx out of range).
                break
            seen.add(email)
            found.append(AccountInfo(index=idx, email=email))
    return found
