# src/app/endpoints/chat.py
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.logger import logger
from schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import get_translate_session_manager
from app.services.fallback import (
    is_g4f_available,
    is_sticky_active,
    call_g4f_fallback,
    FALLBACK_MODEL,
)
from gemini_webapi.exceptions import AuthError, TimeoutError as GeminiTimeoutError

router = APIRouter()

# --- Tool-calling shim ----------------------------------------------------
# gemini-webapi only speaks free-form text. We tell Gemini to emit a delimited
# JSON block per tool invocation, and parse those blocks back into
# OpenAI-shaped `tool_calls[]` so clients (OpenCode, Vercel AI SDK, etc.) see
# native function calling.

_TOOL_CALL_RE = re.compile(r"<<TOOL_CALL>>\s*(\{.*?\})\s*<<END>>", re.DOTALL)
# Backup: catch the OpenCode-prompt-leaked text format `[tool_call:<name> for <args>]`.
_TEXT_TOOL_CALL_RE = re.compile(r"\[tool_call:\s*(\w+)\s+for\s+(.+?)\]", re.DOTALL)


def _build_tools_system_prompt(tools: list[dict]) -> str:
    rendered = []
    for t in tools:
        # Accept both nested {type:"function", function:{...}} and flat {name, ...}.
        fn = t.get("function") or t
        desc_lines = (fn.get("description", "") or "").splitlines()
        first_line = desc_lines[0][:200] if desc_lines else ""
        rendered.append(
            f"- name: {fn.get('name', '?')}\n"
            f"  description: {first_line}\n"
            f"  parameters (JSON Schema): {json.dumps(fn.get('parameters', {}))[:500]}"
        )
    # Pick the first real tool name as a concrete example (OpenCode's first is usually `bash`).
    first_real = None
    for t in tools:
        fn = t.get("function") or t
        n = fn.get("name")
        if n and n != "?":
            first_real = n
            break
    example_name = first_real or "bash"
    return (
        "TOOL CALLING PROTOCOL — read this carefully, it overrides any other "
        "tool-call format mentioned earlier in this conversation:\n\n"
        f"You have access to the following {len(tools)} tools (use these EXACT names):\n\n"
        + "\n".join(rendered)
        + "\n\n"
        "When you want to invoke a tool, output a delimited JSON block, "
        "and ONLY that block (no prose, no markdown fences):\n"
        "<<TOOL_CALL>>\n"
        '{"name": "<exact_tool_name>", "arguments": {<args object matching the parameters schema>}}\n'
        "<<END>>\n\n"
        f"Concrete example (replace fields with what you need):\n"
        "<<TOOL_CALL>>\n"
        f'{{"name": "{example_name}", "arguments": {{"command": "ls -F", "description": "List files"}}}}\n'
        "<<END>>\n\n"
        "Rules:\n"
        "1. The `name` MUST be one of the tools listed above, spelled exactly. "
        "Do NOT invent abstract tool names like 'ls' or 'glob' — pick the real "
        "one (e.g. `bash` for shell commands, `read` for files).\n"
        "2. The `arguments` object MUST match the `parameters` schema of that tool.\n"
        "3. Emit multiple <<TOOL_CALL>>...<<END>> blocks back-to-back to invoke "
        "tools in parallel. The system will execute each one and feed results "
        "back in the next turn (as `Tool result (call_id=...)`).\n"
        "4. Do NOT use the legacy `[tool_call: name for ...]` text format — it "
        "is not parsed and your tool call will be lost.\n"
        "5. When you have enough information to answer the user, write plain "
        "prose with no <<TOOL_CALL>> blocks.\n"
        "6. CRITICAL: when a `Tool result (call_id=...)` is present, ground "
        "your answer strictly in that output. Never substitute prior knowledge "
        "of similar-named projects or libraries. If the tool result contradicts "
        "your priors, trust the tool result.\n"
    )


def _extract_tool_calls(text: str, tool_names: set[str]) -> list[dict]:
    """Parse <<TOOL_CALL>>...<<END>> blocks. Tolerant to missing <<END>> (Gemini sometimes
    forgets it). Falls back to legacy [tool_call:name for args] text format if needed."""
    out = []

    # Walk every <<TOOL_CALL>> marker; for each, parse JSON greedily up to the next marker
    # or end of text. raw_decode stops at the end of the first valid JSON object, so an
    # optional trailing <<END>> is naturally ignored.
    markers = [m.start() for m in re.finditer(r"<<TOOL_CALL>>", text)]
    for i, start in enumerate(markers):
        end_pos = markers[i + 1] if i + 1 < len(markers) else len(text)
        chunk = text[start + len("<<TOOL_CALL>>"):end_pos].lstrip()
        try:
            payload, _consumed = json.JSONDecoder().raw_decode(chunk)
        except json.JSONDecodeError as e:
            logger.warning(f"[shim] JSON decode failed in <<TOOL_CALL>>: {e} — chunk[:200]={chunk[:200]!r}")
            continue
        name = payload.get("name")
        args = payload.get("arguments", {})
        if not name:
            continue
        out.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })

    # Fallback: legacy [tool_call: name for args] only if no JSON blocks matched.
    if not out and tool_names:
        for m in _TEXT_TOOL_CALL_RE.finditer(text):
            name_guess, args_text = m.group(1), m.group(2).strip()
            mapped = name_guess if name_guess in tool_names else (
                "bash" if "bash" in tool_names and name_guess in {"ls", "cat", "find", "grep", "shell"} else None
            )
            if not mapped:
                continue
            args = {"command": args_text.strip("'\" ")} if mapped == "bash" else {"_raw": args_text}
            out.append({
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": mapped, "arguments": json.dumps(args)},
            })
    return out


# --- Debug logging --------------------------------------------------------
# Two levels:
#   - Always-on: high-signal events (REQ.HEAD, SHIM, TRUNCATE, GEMINI.ERR,
#     GEMINI.OK summary, RESP.* mode). Goes to console only.
#   - Gated by GEMINI_BRIDGE_DEBUG=1: verbose dumps (REQ.TOOL, REQ.MSG,
#     PROMPT, full response body). Console + /tmp/gemini-bridge-debug.log.
_VERBOSE_DEBUG = os.environ.get("GEMINI_BRIDGE_DEBUG", "").lower() in ("1", "true", "yes")
_DEBUG_LOG_PATH = Path("/tmp/gemini-bridge-debug.log")
_DEBUG_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB rotation cap


def _dlog(tag: str, _verbose: bool = False, **fields) -> None:
    """Structured log. _verbose=True entries are dropped unless GEMINI_BRIDGE_DEBUG=1."""
    if _verbose and not _VERBOSE_DEBUG:
        return
    line = f"[{tag}] " + " | ".join(f"{k}={v!r}" if not isinstance(v, str) else f"{k}={v}" for k, v in fields.items())
    logger.info(line)
    if _VERBOSE_DEBUG:
        try:
            if _DEBUG_LOG_PATH.exists() and _DEBUG_LOG_PATH.stat().st_size > _DEBUG_LOG_MAX_BYTES:
                _DEBUG_LOG_PATH.rename(_DEBUG_LOG_PATH.with_suffix(".log.1"))
            with _DEBUG_LOG_PATH.open("a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        except Exception:
            pass


def _truncate(s: str | None, n: int = 800) -> str:
    if s is None:
        return "<None>"
    if len(s) <= n:
        return s
    return f"{s[:n//2]}…[TRUNCATED {len(s)-n} chars]…{s[-n//2:]}"


# --- Tool-result truncation ----------------------------------------------
# Gemini Web silently aborts requests when the total prompt is too large.
# Per-tier ceilings (Google docs):
#   - free       : 32k tokens  ≈ 128k chars  → keep tool results small (8k)
#   - Pro (-plus): 1M tokens   ≈ 4M chars    → can afford 32k per result
#   - Ultra (-advanced): same 1M → 128k per result
# A single env override `GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS` collapses all
# tiers to the same cap if set (legacy behavior).
_TIER_CAPS = {"free": 8_000, "plus": 32_000, "advanced": 128_000}
_EXPLICIT_CAP = os.environ.get("GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS")
MAX_TOOL_RESULT_CHARS = int(_EXPLICIT_CAP) if _EXPLICIT_CAP else _TIER_CAPS["free"]  # legacy export

# Hard timeout on a single Gemini call. gemini-webapi has its own retry
# decorator that can stretch a doomed request to 60-120s while it re-inits
# the client between attempts. We short-circuit at this duration so the
# auto-fallback to g4f kicks in fast.
GEMINI_REQUEST_TIMEOUT = float(os.environ.get("GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS", "30"))


def _cap_for_model(model: str) -> int:
    if _EXPLICIT_CAP:
        return int(_EXPLICIT_CAP)
    if model.endswith("-advanced"):
        return _TIER_CAPS["advanced"]
    if model.endswith("-plus"):
        return _TIER_CAPS["plus"]
    return _TIER_CAPS["free"]


def _maybe_truncate_tool_result(content: str, cap: int = MAX_TOOL_RESULT_CHARS) -> tuple[str, bool]:
    if not isinstance(content, str) or len(content) <= cap:
        return content, False
    head = cap // 2
    tail = cap - head - 80
    return (
        content[:head]
        + f"\n\n…[gemini-bridge truncated {len(content) - cap} chars to stay under upstream prompt limits]…\n\n"
        + content[-tail:],
        True,
    )


def _map_gemini_error(exc: Exception) -> HTTPException:
    """Map an exception from the Gemini layer to the right HTTP status."""
    msg = str(exc).lower()
    if isinstance(exc, AuthError):
        return HTTPException(401, f"Gemini auth failed (cookies expired?): {exc}")
    if isinstance(exc, GeminiTimeoutError) or "timeout" in msg:
        return HTTPException(504, f"Gemini upstream timed out: {exc}")
    if any(k in msg for k in ("usage limit", "quota", "rate limit", "too many requests", "exceeded", "status: 429")):
        return HTTPException(429, f"Gemini usage limit reached: {exc}")
    if "status: 401" in msg or "status: 403" in msg:
        return HTTPException(401, f"Gemini auth refused: {exc}")
    # Captcha / abuse-detection wall: Gemini redirects to /sorry/index (Status: 302)
    # or returns Status: 0 when the redirect is blocked. Treat as quota-like so the
    # auto-fallback engages.
    if "status: 302" in msg or "sorry" in msg:
        return HTTPException(429, f"Gemini captcha wall (abuse detection): {exc}")
    return HTTPException(502, f"Gemini upstream error: {exc}")

@router.get("/v1/gems")
async def list_gems():
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        gems = await gemini_client.fetch_gems()
        return {
            "gems": [
                {
                    "id": gem.id,
                    "name": gem.name,
                    "description": gem.description,
                    "predefined": gem.predefined,
                }
                for gem in gems
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching gems: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching gems: {str(e)}")

@router.post("/translate")
async def translate_chat(request: GeminiRequest):
    try:
        get_gemini_client()  # raises if not initialized
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    session_manager = get_translate_session_manager()
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager is not initialized.")
    try:
        # This call now correctly uses the fixed session manager
        response = await session_manager.get_response(request.model, request.message, request.files)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise _map_gemini_error(e)

def convert_to_openai_format(response_text: str, model: str, tool_calls: list[dict] | None = None):
    if tool_calls:
        message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": response_text}
        finish = "stop"
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_openai_format(response_text: str, model: str, tool_calls: list[dict] | None = None):
    """Simulate SSE streaming from a complete response, compatible with OpenAI streaming format."""
    chunk_id = f"chatcmpl-{int(time.time())}"
    created = int(time.time())
    first = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
    }
    yield f"data: {json.dumps(first)}\n\n"

    if tool_calls:
        delta_calls = [
            {
                "index": i,
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]},
            }
            for i, tc in enumerate(tool_calls)
        ]
        chunk = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"tool_calls": delta_calls}, "finish_reason": None}]
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        end = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
        }
        yield f"data: {json.dumps(end)}\n\n"
        yield "data: [DONE]\n\n"
        return

    content = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": response_text}, "finish_reason": None}]
    }
    yield f"data: {json.dumps(content)}\n\n"
    end = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    }
    yield f"data: {json.dumps(end)}\n\n"
    yield "data: [DONE]\n\n"

def _is_gemini_model(name: str) -> bool:
    return name.startswith("gemini-")


@router.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    is_stream = request.stream if request.stream is not None else False

    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")
    if not request.model:
        raise HTTPException(status_code=400, detail="Model not specified in the request.")

    req_id = uuid.uuid4().hex[:8]
    _dlog("REQ.HEAD", req=req_id, model=request.model, stream=is_stream,
          msgs=len(request.messages),
          tools=(len(request.tools) if request.tools else 0),
          tool_choice=str(request.tool_choice) if request.tool_choice else None)

    # Passthrough: any non-Gemini model ID goes straight to g4f, no sticky tracking.
    # Lets the user pick free no-auth models (command-r-plus, qwen-3-235b, …) directly
    # in OpenCode without waiting for Gemini to fail.
    if not _is_gemini_model(request.model):
        if not is_g4f_available():
            raise HTTPException(503, f"Model '{request.model}' is non-Gemini but g4f is not installed.")
        from fastapi.responses import JSONResponse
        _dlog("PASSTHROUGH", req=req_id, model=request.model)
        logger.info(f"Passthrough → g4f ({request.model})")
        try:
            g4f_resp = await call_g4f_fallback(
                request.messages, request.tools,
                reason="explicit", model=request.model, arm_sticky=False,
            )
        except Exception as e:
            _dlog("PASSTHROUGH.ERR", req=req_id, err=_truncate(str(e), 300))
            logger.error(f"g4f passthrough failed for {request.model}: {e}")
            raise HTTPException(502, f"g4f passthrough failed: {e}")
        headers = {"X-Bridge-Fallback": f"g4f:{request.model}:explicit"}
        if is_stream:
            msg = (g4f_resp.get("choices", [{}])[0].get("message", {}) or {})
            return StreamingResponse(
                stream_openai_format(msg.get("content") or "", request.model, msg.get("tool_calls") or None),
                media_type="text/event-stream",
                headers=headers,
            )
        return JSONResponse(content=g4f_resp, headers=headers)

    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if request.tools and _VERBOSE_DEBUG:
        for i, t in enumerate(request.tools[:3]):
            fn = t.get("function") or t
            _dlog("REQ.TOOL", _verbose=True, req=req_id, idx=i,
                  name=fn.get("name", "?"),
                  desc_excerpt=_truncate(fn.get("description", ""), 200),
                  params_keys=list((fn.get("parameters", {}) or {}).get("properties", {}).keys())[:8])

    if _VERBOSE_DEBUG:
        for i, msg in enumerate(request.messages):
            role = msg.get("role", "?")
            content = msg.get("content")
            tcs = msg.get("tool_calls")
            tcid = msg.get("tool_call_id")
            _dlog("REQ.MSG", _verbose=True, req=req_id, idx=i, role=role, tcid=tcid,
                  tool_calls_count=(len(tcs) if tcs else 0),
                  content_len=(len(content) if isinstance(content, str) else None),
                  content_excerpt=_truncate(content if isinstance(content, str) else json.dumps(content), 600))

    conversation_parts = []
    truncated_tool_results = 0
    total_truncated_chars = 0
    cap = _cap_for_model(request.model)
    for msg in request.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        tcs = msg.get("tool_calls")
        if role == "system" and content:
            conversation_parts.append(f"System: {content}")
        elif role == "user" and content:
            conversation_parts.append(f"User: {content}")
        elif role == "assistant":
            if tcs:
                blocks = []
                for tc in tcs:
                    fn = tc.get("function") or {}
                    try:
                        args_obj = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args_obj = {}
                    blocks.append("<<TOOL_CALL>>\n" + json.dumps({"name": fn.get("name"), "arguments": args_obj}) + "\n<<END>>")
                prefix = f"Assistant: {content}\n" if content else "Assistant: "
                conversation_parts.append(prefix + "\n".join(blocks))
            elif content:
                conversation_parts.append(f"Assistant: {content}")
        elif role == "tool":
            tcid = msg.get("tool_call_id", "?")
            tool_content = content or ""
            new_content, was_truncated = _maybe_truncate_tool_result(tool_content, cap)
            if was_truncated:
                truncated_tool_results += 1
                total_truncated_chars += len(tool_content) - len(new_content)
            conversation_parts.append(f"Tool result (call_id={tcid}):\n{new_content}")
    if truncated_tool_results:
        _dlog("TRUNCATE", req=req_id, results_truncated=truncated_tool_results,
              chars_dropped=total_truncated_chars, max_per_result=cap)

    if not conversation_parts:
        raise HTTPException(status_code=400, detail="No valid messages found.")

    # Inject the tool-calling protocol as a LATE system block so it overrides any
    # earlier conventions baked into the client's huge system prompt.
    if request.tools:
        conversation_parts.append("System (tool-calling protocol — overrides earlier instructions):\n"
                                  + _build_tools_system_prompt(request.tools))

    final_prompt = "\n\n".join(conversation_parts)
    _dlog("PROMPT", _verbose=True, req=req_id, total_chars=len(final_prompt),
          head=_truncate(final_prompt[:1500], 1500),
          tail=_truncate(final_prompt[-1500:], 1500))

    async def _serve_via_g4f(reason: str, origin_err: Optional[Exception] = None):
        from fastapi.responses import JSONResponse
        _dlog("FALLBACK.TRY", req=req_id, reason=reason, model=FALLBACK_MODEL,
              origin_err=_truncate(str(origin_err), 200) if origin_err else None)
        if origin_err:
            logger.warning(f"Gemini {reason} → g4f fallback engaged ({FALLBACK_MODEL}): {origin_err}")
        else:
            logger.info(f"Sticky fallback active → g4f ({FALLBACK_MODEL}) — bypassing Gemini")
        g4f_resp = await call_g4f_fallback(request.messages, request.tools, reason)
        _dlog("FALLBACK.OK", req=req_id, latency_s=round(time.time() - t0, 2))
        headers = {"X-Bridge-Fallback": f"g4f:{FALLBACK_MODEL}:{reason}"}
        # Annotate the model field so the OpenCode UI surfaces the fallback.
        annotated_model = f"{request.model}→g4f:{FALLBACK_MODEL}"
        if is_stream:
            msg = (g4f_resp.get("choices", [{}])[0].get("message", {}) or {})
            return StreamingResponse(
                stream_openai_format(msg.get("content") or "", annotated_model, msg.get("tool_calls") or None),
                media_type="text/event-stream",
                headers=headers,
            )
        if isinstance(g4f_resp, dict):
            g4f_resp["model"] = annotated_model
        return JSONResponse(content=g4f_resp, headers=headers)

    t0 = time.time()

    # Sticky shortcut: if a recent fallback flipped Gemini off, skip the
    # Gemini call entirely until the sticky window expires.
    if is_sticky_active() and is_g4f_available():
        try:
            return await _serve_via_g4f(reason="sticky")
        except Exception as fb_e:
            _dlog("FALLBACK.ERR", req=req_id, sticky=True, err=_truncate(str(fb_e), 300))
            logger.error(f"Sticky fallback failed, falling back to Gemini: {fb_e}")
            # fallthrough — try Gemini as last resort

    try:
        response = await asyncio.wait_for(
            gemini_client.generate_content(message=final_prompt, model=request.model, files=None),
            timeout=GEMINI_REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # Map asyncio's TimeoutError to a Gemini-shaped exception so the existing
        # 504 → fallback path engages.
        e = GeminiTimeoutError(f"Gemini request exceeded {GEMINI_REQUEST_TIMEOUT}s (bridge-side cutoff)")
        mapped = _map_gemini_error(e)
        if mapped.status_code in (429, 401, 502, 504) and is_g4f_available():
            try:
                return await _serve_via_g4f(reason="timeout", origin_err=e)
            except Exception as fb_e:
                _dlog("FALLBACK.ERR", req=req_id, err=_truncate(str(fb_e), 300))
                logger.error(f"g4f fallback after timeout failed: {fb_e}")
                raise mapped
        raise mapped
    except Exception as e:
        mapped = _map_gemini_error(e)
        if mapped.status_code in (429, 401, 502, 504) and is_g4f_available():
            reason = {429: "quota", 401: "auth", 502: "upstream", 504: "timeout"}.get(mapped.status_code, "error")
            try:
                return await _serve_via_g4f(reason=reason, origin_err=e)
            except Exception as fb_e:
                _dlog("FALLBACK.ERR", req=req_id, err=_truncate(str(fb_e), 300))
                logger.error(f"g4f fallback failed: {fb_e}")
                raise mapped
        _dlog("GEMINI.ERR", req=req_id, err_type=type(e).__name__, err=_truncate(str(e), 500))
        logger.error(f"Error in /v1/chat/completions endpoint: {e}", exc_info=True)
        raise mapped

    raw_text = response.text or ""
    _dlog("GEMINI.OK", req=req_id, latency_s=round(time.time() - t0, 2), resp_chars=len(raw_text))
    _dlog("GEMINI.BODY", _verbose=True, req=req_id, resp_full=_truncate(raw_text, 2500))

    tool_names: set[str] = set()
    if request.tools:
        for t in request.tools:
            fn = t.get("function") or t
            n = fn.get("name")
            if n:
                tool_names.add(n)
    tool_calls = _extract_tool_calls(raw_text, tool_names) if request.tools else []
    final_text = _TOOL_CALL_RE.sub("", _TEXT_TOOL_CALL_RE.sub("", raw_text)).strip() if tool_calls else raw_text
    _dlog("SHIM", req=req_id, tool_calls_extracted=len(tool_calls),
          first_call=(json.dumps(tool_calls[0]) if tool_calls else None),
          fallback_used=bool(tool_calls and not _TOOL_CALL_RE.search(raw_text)))

    if is_stream:
        _dlog("RESP.STREAM", req=req_id, mode="sse", with_tool_calls=bool(tool_calls))
        return StreamingResponse(
            stream_openai_format(final_text, request.model, tool_calls or None),
            media_type="text/event-stream",
        )
    _dlog("RESP.JSON", req=req_id, mode="json", with_tool_calls=bool(tool_calls))
    return convert_to_openai_format(final_text, request.model, tool_calls or None)
