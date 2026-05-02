"""Route guards. Centralises the extension-only check so handlers stay clean."""
from litestar.connection import ASGIConnection
from litestar.exceptions import HTTPException
from litestar.handlers.base import BaseRouteHandler


def extension_only(connection: ASGIConnection, _handler: BaseRouteHandler) -> None:
    """Accept browser-extension Origin or X-Extension-Id (Chrome strips Origin on host_permissions GETs)."""
    origin = connection.headers.get("origin")
    if origin and origin.startswith(("chrome-extension://", "moz-extension://")):
        return
    if connection.headers.get("x-extension-id"):
        return
    raise HTTPException(
        status_code=403,
        detail="Origin must be a browser-extension:// URL or request must carry X-Extension-Id header.",
    )
