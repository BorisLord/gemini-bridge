from typing import ClassVar

from app.endpoints._shared import registry
from app.logger import logger
from app.services.bootstrap import bootstrap_browser_accounts
from litestar import Controller, Request, get, post
from pydantic import BaseModel


class DiscoveredAccount(BaseModel):
    id: str
    source: str
    email: str | None = None
    client_active: bool = False
    selected_gem_id: str | None = None


class AccountsController(Controller):
    """Headless cross-account flow. Loopback bind is the security boundary —
    these endpoints intentionally have no extension guard so the bridge stays
    usable from CLI / scripts without touching the extension."""
    path = "/accounts"
    tags: ClassVar = ["accounts"]

    @get("/", summary="List every Gemini account in the registry")
    async def list_registered(self, request: Request) -> list[DiscoveredAccount]:
        reg = registry(request)
        return [
            DiscoveredAccount(
                id=a.id,
                source=a.source,
                email=a.email,
                client_active=a.client is not None,
                selected_gem_id=a.selected_gem_id,
            )
            for a in reg.list()
        ]

    @post(
        "/refresh",
        status_code=200,
        summary="Re-run cross-browser discovery and upsert any new accounts",
    )
    async def refresh(self, request: Request) -> dict:
        reg = registry(request)
        before = {a.id for a in reg.list()}
        upserted = await bootstrap_browser_accounts(reg)
        after = {a.id for a in reg.list()}
        added = sorted(after - before)
        logger.info(f"/accounts/refresh: probed {upserted} browser accounts, {len(added)} new ({added})")
        return {"upserted": upserted, "added": added, "total": len(after)}
