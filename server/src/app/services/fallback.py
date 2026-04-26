"""Auto-fallback to g4f when Gemini quotas are exhausted.

When /v1/chat/completions hits a quota (429) or auth failure on Gemini and
g4f is installed, we route the same OpenAI request through g4f.client in the
same HTTP request so the client (OpenCode) just gets a working response. The
event is recorded so the extension popup can surface a discreet indicator.
"""

import asyncio
import os
import time
from typing import Any, Optional

# In 2026 most OpenAI-route providers in g4f require auth (HAR file, OAuth, API key).
# We default to a model whose providers in g4f are no-auth public ones:
# command-r-plus is served by CohereForAI / HuggingSpace, no API key required, and
# Cohere's Command R+ is purpose-built for tool calling & RAG.
DEFAULT_FALLBACK_MODEL = "command-r-plus"
FALLBACK_MODEL = os.environ.get("GEMINI_BRIDGE_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
# Optional API key forwarded to g4f.client.Client(api_key=...). Used when the
# chosen model needs auth (e.g. OpenRouter `openai/gpt-4o` with an OpenRouter key).
# g4f does NOT read provider env vars (OPENROUTER_API_KEY, etc.) by itself.
FALLBACK_API_KEY = os.environ.get("GEMINI_BRIDGE_FALLBACK_API_KEY")
# Sticky window: after a successful auto-fallback, skip Gemini entirely for this many hours.
# 0 disables the sticky behavior (every request retries Gemini first).
STICKY_HOURS = float(os.environ.get("GEMINI_BRIDGE_FALLBACK_STICKY_HOURS", "4"))

_LAST_EVENT: dict = {"at": None, "reason": None, "model": None, "ok": None, "error": None}
_STICKY_UNTIL: Optional[float] = None  # epoch seconds when the sticky window expires


def is_g4f_available() -> bool:
    try:
        import g4f  # noqa: F401
        return True
    except ImportError:
        return False


def is_sticky_active() -> bool:
    return _STICKY_UNTIL is not None and time.time() < _STICKY_UNTIL


def sticky_until() -> Optional[float]:
    return _STICKY_UNTIL if is_sticky_active() else None


def reset_sticky() -> None:
    global _STICKY_UNTIL
    _STICKY_UNTIL = None


def get_last_fallback_event() -> dict:
    out = dict(_LAST_EVENT)
    out["sticky_until"] = sticky_until()
    out["sticky_hours"] = STICKY_HOURS
    return out


def _record(reason: str, ok: bool, model: str, error: Optional[str] = None,
            arm_sticky: bool = True) -> None:
    global _STICKY_UNTIL
    _LAST_EVENT.update({
        "at": time.time(),
        "reason": reason,
        "model": model,
        "ok": ok,
        "error": error,
    })
    if ok and arm_sticky and STICKY_HOURS > 0:
        _STICKY_UNTIL = time.time() + STICKY_HOURS * 3600


async def call_g4f_fallback(
    messages: list[dict],
    tools: Optional[list[dict]],
    reason: str,
    model: Optional[str] = None,
    arm_sticky: bool = True,
) -> dict:
    """Forward the OpenAI request to g4f.client, return its response dict.

    `model` defaults to FALLBACK_MODEL (auto-fallback path). chat.py overrides
    when the client explicitly requests a non-Gemini model (passthrough path);
    in that case `arm_sticky=False` so the explicit call doesn't flip the
    Gemini-skip window.
    """
    from g4f.client import Client  # type: ignore[import-not-found]

    target_model = model or FALLBACK_MODEL

    def _sync() -> Any:
        client = Client(api_key=FALLBACK_API_KEY) if FALLBACK_API_KEY else Client()
        kwargs: dict = {"model": target_model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        return client.chat.completions.create(**kwargs)

    try:
        resp = await asyncio.to_thread(_sync)
    except Exception as e:
        _record(reason, ok=False, model=target_model, error=str(e)[:300], arm_sticky=arm_sticky)
        raise
    _record(reason, ok=True, model=target_model, arm_sticky=arm_sticky)
    # g4f returns an object with .model_dump() (pydantic) or already a dict.
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    return resp  # type: ignore[return-value]
