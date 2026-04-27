"""Auto-fallback to OpenRouter (free models) when Gemini quotas are exhausted."""

import json
import os
import time
from typing import Optional

import httpx

from app.config import CONFIG

# Free OpenRouter models with tool/function-calling support (verified against
# /api/v1/models on 2026-04-27 — all `:free` and declare `tools`). OpenRouter
# rotates this catalogue; if a model 404s, swap or pick another in the popup.
DEFAULT_FREE_MODELS = [
    "qwen/qwen3-coder:free",
    "z-ai/glm-4.5-air:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERRER = "https://github.com/gemini-bridge"
OPENROUTER_APP_TITLE = "gemini-bridge"
OPENROUTER_REQUEST_TIMEOUT = float(
    os.environ.get("GEMINI_BRIDGE_OPENROUTER_TIMEOUT_SECONDS", "60")
)


def _initial_enabled() -> bool:
    env = os.environ.get("GEMINI_BRIDGE_FALLBACK_ENABLED")
    if env is not None:
        return env.lower() in ("1", "true", "yes")
    cfg = CONFIG["OpenRouter"].get("enabled", "true") if "OpenRouter" in CONFIG else "true"
    return cfg.lower() in ("1", "true", "yes")


def _initial_api_key() -> Optional[str]:
    if k := os.environ.get("OPENROUTER_API_KEY"):
        return k.strip() or None
    if "OpenRouter" in CONFIG:
        if k := CONFIG["OpenRouter"].get("api_key", "").strip():
            return k
    return None


def _initial_model() -> str:
    if m := os.environ.get("GEMINI_BRIDGE_FALLBACK_MODEL"):
        return m
    if "OpenRouter" in CONFIG:
        if m := CONFIG["OpenRouter"].get("model", "").strip():
            return m
    return DEFAULT_FREE_MODELS[0]


_state: dict = {
    "enabled": _initial_enabled(),
    "api_key": _initial_api_key(),
    "model": _initial_model(),
}

# After one successful auto-fallback, skip Gemini for this many hours (0 disables).
STICKY_HOURS = float(os.environ.get("GEMINI_BRIDGE_FALLBACK_STICKY_HOURS", "1"))

_LAST_EVENT: dict = {"at": None, "reason": None, "model": None, "ok": None, "error": None}
_STICKY_UNTIL: Optional[float] = None


def is_enabled() -> bool:
    return bool(_state["enabled"])


def has_api_key() -> bool:
    return bool(_state["api_key"])


def is_available() -> bool:
    return is_enabled() and has_api_key()


def get_model() -> str:
    return _state["model"]


def set_enabled(enabled: bool) -> None:
    _state["enabled"] = bool(enabled)


def set_api_key(api_key: Optional[str]) -> None:
    _state["api_key"] = (api_key or "").strip() or None


def set_model(model: str) -> None:
    if not model:
        raise ValueError("model cannot be empty")
    _state["model"] = model


def get_public_state() -> dict:
    """Safe view for the popup — never expose the raw API key."""
    key = _state["api_key"]
    return {
        "enabled": _state["enabled"],
        "model": _state["model"],
        "has_api_key": bool(key),
        "api_key_masked": (f"{key[:7]}…{key[-4:]}" if key and len(key) > 14 else None),
        "available_models": DEFAULT_FREE_MODELS,
        "sticky_hours": STICKY_HOURS,
    }


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


class FallbackDisabledError(RuntimeError):
    """Raised when fallback is requested but disabled (toggle OFF or no key)."""


async def call_openrouter_fallback(
    messages: list[dict],
    tools: Optional[list[dict]],
    reason: str,
    model: Optional[str] = None,
    arm_sticky: bool = True,
) -> dict:
    """Forward the OpenAI request to OpenRouter. Pass `model` for the passthrough
    path (non-Gemini ID requested by the client) with `arm_sticky=False`."""
    if not is_enabled():
        raise FallbackDisabledError("OpenRouter fallback is disabled (toggle in extension popup).")
    if not has_api_key():
        raise FallbackDisabledError(
            "OpenRouter fallback enabled but no API key configured. "
            "Set OPENROUTER_API_KEY, add [OpenRouter] api_key=… to config.conf, "
            "or paste a key in the extension popup."
        )

    target_model = model or _state["model"]
    payload: dict = {"model": target_model, "messages": messages}
    if tools:
        payload["tools"] = tools

    headers = {
        "Authorization": f"Bearer {_state['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERRER,
        "X-Title": OPENROUTER_APP_TITLE,
    }

    # One _record per outcome — recording inside an intermediate raise would
    # double-log via the broad `except Exception` below.
    try:
        async with httpx.AsyncClient(timeout=OPENROUTER_REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:400]}")
        try:
            data = r.json()
        except json.JSONDecodeError as je:
            raise RuntimeError(
                f"OpenRouter returned non-JSON body (status={r.status_code}): {r.text[:400]}"
            ) from je
    except FallbackDisabledError:
        raise
    except Exception as e:
        _record(reason, ok=False, model=target_model, error=str(e)[:300], arm_sticky=arm_sticky)
        raise

    _record(reason, ok=True, model=target_model, arm_sticky=arm_sticky)
    return data
