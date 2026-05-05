"""Subclass of `gemini_webapi.GeminiClient` adding /u/{N}/ account routing.

Subclass (not composition): the lib's `@running` decorator silently re-inits
the client when `auto_close` fires, rebuilding `AsyncSession` from scratch.
Overriding `init()` is the only way to re-install the router patch on every
re-init. Cookie rotation is delegated to the lib (`auto_refresh=True`)."""
from urllib.parse import urlparse, urlunparse

from app.logger import logger
from gemini_webapi import GeminiClient as WebGeminiClient


def _inject_account_index(url: str, idx: int) -> str:
    """Rewrite https://gemini.google.com/<path> -> /u/{idx}/<path>.
    Idempotent if the path already starts with /u/{N}/."""
    if idx <= 0 or "gemini.google.com" not in url:
        return url
    parsed = urlparse(url)
    if parsed.netloc != "gemini.google.com":
        return url
    path = parsed.path
    parts = path.lstrip("/").split("/", 2)
    if len(parts) >= 2 and parts[0] == "u" and parts[1].isdigit():
        return url
    new_path = f"/u/{idx}{path}"
    return urlunparse(parsed._replace(path=new_path))


class AccountRoutedGeminiClient(WebGeminiClient):
    """`GeminiClient` that routes every Gemini request through `/u/{account_index}/`
    and forwards extra Google session cookies (SID/HSID/SAPISID/…) needed by
    workspace accounts."""

    def __init__(
        self,
        secure_1psid: str,
        secure_1psidts: str,
        proxy: str | None = None,
        account_index: int = 0,
        extra_cookies: dict | None = None,
    ) -> None:
        super().__init__(secure_1psid, secure_1psidts, proxy)
        self._account_index = account_index
        self._extra_cookies = extra_cookies

    async def init(self, *args, **kwargs) -> None:
        await super().init(*args, **kwargs)
        # `gemini-webapi` only stores 1PSID/1PSIDTS — workspace accounts often
        # need the full Google session jar for RPCs to report AUTHENTICATED.
        if self._extra_cookies:
            extras = {
                k: v
                for k, v in self._extra_cookies.items()
                if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and v
            }
            if extras:
                self.cookies = extras  # setter; sets domain=.google.com
        if self._account_index > 0:
            self._install_account_router()

    def _install_account_router(self) -> None:
        """Wrap AsyncSession.request to inject /u/{N}/ in Gemini URLs."""
        session = self.client
        if session is None:
            return
        if getattr(session, "_account_routed", False):
            return
        original_request = session.request
        idx = self._account_index

        async def routed_request(method, url, *args, **kwargs):
            return await original_request(method, _inject_account_index(url, idx), *args, **kwargs)

        session.request = routed_request
        session._account_routed = True
        logger.info(f"Account router installed: requests will hit /u/{idx}/...")
