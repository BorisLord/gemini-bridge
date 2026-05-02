"""Route guards. Centralises the extension-only check so handlers stay clean."""
from litestar.connection import ASGIConnection
from litestar.exceptions import HTTPException
from litestar.handlers.base import BaseRouteHandler


def extension_only(connection: ASGIConnection, _handler: BaseRouteHandler) -> None:
    """Accept iff Origin is a {chrome,moz}-extension:// URL OR X-Extension-Id is set.
    The latter covers GETs where Chrome strips Origin (host_permissions, same-origin-like).
    CSRF/inter-extension hygiene — not real authn (both signals are spoofable)."""
    origin = connection.headers.get("origin")
    if origin and origin.startswith(("chrome-extension://", "moz-extension://")):
        return
    if connection.headers.get("x-extension-id"):
        return
    raise HTTPException(
        status_code=403,
        detail="Origin must be a browser-extension:// URL or request must carry X-Extension-Id header.",
    )
