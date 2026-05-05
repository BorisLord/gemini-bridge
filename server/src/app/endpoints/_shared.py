"""Shared helpers for auth/runtime/accounts controllers."""
from app.schemas.openai_chat import err_response
from app.services.account_registry import AccountRegistry
from litestar import Request

FORBIDDEN = err_response("Origin is not chrome-extension:// and X-Extension-Id is missing.")


def registry(request: Request) -> AccountRegistry:
    return request.app.state.account_registry
