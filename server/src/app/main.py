from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import settings
from app.services.gemini_client import init_gemini_client
from app.logger import logger

from app.endpoints import chat, auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        if await init_gemini_client():
            logger.info("Gemini client initialized in worker process.")
        else:
            logger.warning("Gemini client not initialized — waiting for cookies via /auth/cookies.")
    except Exception as e:
        logger.error(f"Error initializing Gemini client in worker process: {e}")

    logger.info(
        "[BOOT] features active: markdown-arg sanitizer, head-tail prompt trim (cap GEMINI_BRIDGE_MAX_PROMPT_CHARS), "
        f"full-prompt dumps {'on (server/logs/prompts/)' if settings.DUMP_PROMPTS else 'off (set GEMINI_BRIDGE_DUMP_PROMPTS=1)'}"
    )

    yield

    logger.info("Application shutdown complete.")


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(auth.status_router)


@app.get("/healthz")
async def healthz():
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


@app.exception_handler(StarletteHTTPException)
async def _http_exc_handler(_request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=_openai_error_body(exc.status_code, str(exc.detail)),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request: Request, exc: RequestValidationError):
    msg = "; ".join(
        f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg', '')}"
        for e in exc.errors()
    ) or "Invalid request payload."
    return JSONResponse(status_code=422, content=_openai_error_body(422, msg))
