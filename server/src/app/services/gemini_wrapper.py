"""Wrapper around `gemini_webapi.GeminiClient` adding /u/{N}/ account routing
and on-rotation cookie persistence to `config.conf`. Sits below the public
service surface in `services.gemini_client`."""
import configparser
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from app.logger import logger
from gemini_webapi import GeminiClient as WebGeminiClient

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.conf"


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


class BridgeGeminiClient:
    def __init__(
        self,
        secure_1psid: str,
        secure_1psidts: str,
        proxy: str | None = None,
        account_index: int = 0,
        extra_cookies: dict | None = None,
    ) -> None:
        self.client = WebGeminiClient(secure_1psid, secure_1psidts, proxy)
        self.account_index = account_index
        # gemini-webapi only stores 1PSID/1PSIDTS by default. Workspace accounts
        # often need the full Google session (SID, HSID, SAPISID, …) for RPCs
        # to report AUTHENTICATED — forward whatever the extension captures.
        if extra_cookies:
            extras = {k: v for k, v in extra_cookies.items()
                      if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and v}
            if extras:
                self.client.cookies = extras  # setter; sets domain=.google.com

    async def init(self) -> None:
        await self.client.init()
        if self.account_index > 0:
            self._install_account_router()
        await self._persist_cookies()

    def _install_account_router(self) -> None:
        """Wrap AsyncSession.request to inject /u/{N}/ in Gemini URLs."""
        session = self.client.client
        if session is None or getattr(session, "_account_routed", False):
            return
        original_request = session.request
        idx = self.account_index

        async def routed_request(method, url, *args, **kwargs):
            return await original_request(method, _inject_account_index(url, idx), *args, **kwargs)

        session.request = routed_request
        session._account_routed = True
        logger.info(f"Account router installed: requests will hit /u/{idx}/...")

    async def _persist_cookies(self) -> None:
        if not _CONFIG_PATH.exists():
            logger.warning(f"Cannot persist cookies: {_CONFIG_PATH} not found.")
            return
        try:
            cookies = self.client.cookies
            psid = cookies.get("__Secure-1PSID")
            psidts = cookies.get("__Secure-1PSIDTS")
            if not psid:
                return
            cfg = configparser.ConfigParser()
            cfg.read(_CONFIG_PATH, encoding="utf-8")
            if "Cookies" not in cfg:
                cfg["Cookies"] = {}
            cfg["Cookies"]["gemini_cookie_1psid"] = psid
            if psidts:
                cfg["Cookies"]["gemini_cookie_1psidts"] = psidts
            # Atomic write so a mid-write crash can't truncate config.conf and
            # lose the rotated cookies.
            tmp = _CONFIG_PATH.with_suffix(_CONFIG_PATH.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                cfg.write(f)
            tmp.chmod(0o600)
            tmp.replace(_CONFIG_PATH)
            logger.info("Cookies persisted to config.conf after rotation.")
        except Exception as e:
            logger.warning(f"Failed to persist cookies: {e}")

    async def generate_content(
        self,
        message: str,
        model: str,
        files: list[str | Path] | None = None,
        gem: str | None = None,
    ):
        return await self.client.generate_content(message, model=model, files=files, gem=gem)

    async def close(self) -> None:
        await self.client.close()
