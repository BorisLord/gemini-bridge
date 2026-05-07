from typing import ClassVar

from app.endpoints._shared import FORBIDDEN, registry
from app.guards import extension_only
from app.logger import logger
from app.schemas.openai_chat import err_response
from app.services.bootstrap import parse_gem_id
from litestar import Controller, Request, get, post
from litestar.exceptions import HTTPException
from pydantic import BaseModel


class GemSelection(BaseModel):
    # Raw Gem ID or full URL; None/"" clears.
    gem_id: str | None = None
    # Required — every Gem update must name its target account.
    account_id: str


class RuntimeController(Controller):
    path = "/runtime"
    guards: ClassVar = [extension_only]
    tags: ClassVar = ["runtime"]

    @get(
        "/status",
        summary="Bridge status — registered accounts + per-account Gem",
        responses={403: FORBIDDEN},
    )
    async def get_status(self, request: Request) -> dict:
        reg = registry(request)
        return {
            "accounts": [
                {
                    "id": a.id,
                    "source": a.source,
                    "email": a.email,
                    "client_active": a.client is not None,
                    "selected_gem_id": a.selected_gem_id,
                }
                for a in reg.list()
            ],
        }

    @post(
        "/gem",
        status_code=200,
        summary="Select / clear active Gem (always scoped via account_id)",
        responses={
            403: FORBIDDEN,
            404: err_response("Unknown account_id."),
            422: err_response("Missing required `account_id`."),
        },
    )
    async def select_gem(self, data: GemSelection, request: Request) -> dict:
        reg = registry(request)
        account = reg.get(data.account_id)
        if account is None:
            raise HTTPException(status_code=404, detail=f"Unknown account_id {data.account_id!r}")
        account.selected_gem_id = parse_gem_id(data.gem_id)
        # No headers in the log line — they're spoofable by any local process
        # and would let a caller plant arbitrary strings into bridge.log.
        logger.info(f"Gem selection updated for {account.id!r}: {account.selected_gem_id!r}")
        return {"account_id": account.id, "selected_id": account.selected_gem_id}
