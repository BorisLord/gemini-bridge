from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app import settings
from app.endpoints.accounts import AccountsController
from app.endpoints.auth import AuthController
from app.endpoints.chat import ChatController
from app.endpoints.runtime import RuntimeController
from app.logger import logger
from app.services.account_registry import AccountRegistry
from app.services.bootstrap import bootstrap_registry
from litestar import Controller, Litestar, Request, get
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.exceptions import HTTPException, ValidationException
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import StoplightRenderPlugin
from litestar.response import Response


@asynccontextmanager
async def _lifespan(app: Litestar) -> AsyncGenerator[None, None]:
    registry = AccountRegistry()
    app.state.account_registry = registry
    await bootstrap_registry(registry)
    accounts = sorted(a.id for a in registry.list())
    if accounts:
        logger.info(f"[BOOT] registry populated: {accounts}")
    else:
        logger.warning(
            "[BOOT] registry empty — no Gemini account available. "
            "Push cookies via the extension, set GEMINI_COOKIE_1PSID/_1PSIDTS, "
            "or sign into gemini.google.com in a supported browser."
        )

    logger.info(
        "[BOOT] features active: markdown-arg sanitizer, head-tail prompt trim "
        "(cap GEMINI_BRIDGE_MAX_PROMPT_CHARS), full-prompt dumps "
        f"{'on (server/logs/prompts/)' if settings.DUMP_PROMPTS else 'off (set GEMINI_BRIDGE_DUMP_PROMPTS=1)'}"
    )
    yield
    # Stop per-account auto_refresh / auto_close tasks before the loop closes,
    # otherwise asyncio logs warnings about pending tasks at shutdown.
    await registry.close_all()
    logger.info("Application shutdown complete.")


class HealthController(Controller):
    """Health check for systemd / Docker / curl. Public, never auth-gated."""

    @get("/healthz", sync_to_thread=False, summary="Health check")
    def healthz(self) -> dict[str, str]:
        return {"status": "ok"}


# Reshape errors to the OpenAI envelope so SDKs that parse `error.message`
# (openai-python, @ai-sdk/openai-compatible) surface the real reason.
_ERROR_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
}


def _openai_error_body(status: int, message: str) -> dict:
    return {
        "error": {
            "message": message,
            "type": _ERROR_TYPE_BY_STATUS.get(status, "api_error"),
            "param": None,
            "code": None,
        }
    }


def _http_exc_handler(_request: Request, exc: HTTPException) -> Response:
    headers = getattr(exc, "headers", None)
    return Response(
        content=_openai_error_body(exc.status_code, exc.detail),
        status_code=exc.status_code,
        headers=headers,
    )


def _validation_handler(_request: Request, exc: ValidationException) -> Response:
    extra = getattr(exc, "extra", None)
    if isinstance(extra, list):
        msg = "; ".join(
            f"{'.'.join(str(x) for x in e.get('key', []) or e.get('loc', []))}: {e.get('message', e.get('msg', ''))}"
            for e in extra
            if isinstance(e, dict)
        ) or (exc.detail or "Invalid request payload.")
    else:
        msg = exc.detail or "Invalid request payload."
    return Response(content=_openai_error_body(422, msg), status_code=422)


# SSE chunks must NOT be gzipped — per-chunk compression buffers content and
# defeats the live-stream semantics for any reverse proxy in front. Excluded by
# path regex; everything else (incl. /v1/models) gets compressed >=500 bytes.
_COMPRESSION = CompressionConfig(backend="gzip", exclude=[r"^/v1/chat/completions$"])

# `allow_origins=[]` is mandatory: Litestar's default `["*"]` flips the
# middleware into "allow-all" and bypasses the regex entirely.
_CORS = CORSConfig(
    allow_origins=[],
    allow_origin_regex=r"^((chrome|moz)-extension://.*|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app = Litestar(
    route_handlers=[HealthController, ChatController, AuthController, RuntimeController, AccountsController],
    lifespan=[_lifespan],
    cors_config=_CORS,
    compression_config=_COMPRESSION,
    openapi_config=(
        OpenAPIConfig(
            title="Gemini Bridge",
            version="0.1.0",
            path="/docs",
            # Stoplight Elements only — Litestar's default UI when one is picked.
            # Empty render_plugins=() would expose all 5 (Swagger/Redoc/Rapidoc/…).
            render_plugins=[StoplightRenderPlugin(path="/")],
        )
        if settings.ENABLE_DOCS
        else None
    ),
    exception_handlers={
        HTTPException: _http_exc_handler,
        ValidationException: _validation_handler,
    },
)
# Default registry for code paths that import `app` without running the
# lifespan (TestClient outside `with`, fresh imports). Replaced at boot.
app.state.account_registry = AccountRegistry()
