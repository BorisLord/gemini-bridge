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
from schemas.request import OpenAIChatRequest
from app.services.gemini_client import (
    get_gemini_client,
    get_selected_gem_id,
    GeminiClientNotInitializedError,
)
from gemini_webapi.exceptions import AuthError, TimeoutError as GeminiTimeoutError

router = APIRouter()

# gemini-webapi only speaks free-form text. We ask Gemini to emit a delimited
# JSON block per tool invocation, then parse those back into OpenAI-shaped
# `tool_calls[]` so clients see native function calling.

_TOOL_CALL_RE = re.compile(r"<<TOOL_CALL>>\s*(\{.*?\})\s*<<END>>", re.DOTALL)
# Backup: OpenCode-prompt-leaked text format `[tool_call:<name> for <args>]`.
_TEXT_TOOL_CALL_RE = re.compile(r"\[tool_call:\s*(\w+)\s+for\s+(.+?)\]", re.DOTALL)

# Gemini-3-pro tends to wrap string arguments as Markdown links `[X](Y)` or
# code spans `` `X` ``, breaking downstream consumers (e.g. webfetch rejects
# bracketed URLs). Strip those wrappers before forwarding the tool call.
_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
_MD_CODE_RE = re.compile(r"^`([^`]+)`$")


def _sanitize_arg_string(s: str) -> str:
    s = s.strip()
    m = _MD_LINK_RE.match(s)
    if m:
        text, target = m.group(1).strip(), m.group(2).strip()
        # Identical halves (Gemini's typical `[https://x](https://x)`): collapse.
        if text == target:
            return target
        # Otherwise keep the target if it looks like a URL or path, else the label.
        if target.startswith(("http://", "https://", "/", "./", "../")) or "/" in target:
            return target
        return text
    m = _MD_CODE_RE.match(s)
    if m:
        return m.group(1).strip()
    return s


def _strip_md_wrappers(value, _changes: Optional[list] = None):
    if isinstance(value, str):
        cleaned = _sanitize_arg_string(value)
        if _changes is not None and cleaned != value:
            _changes.append((value, cleaned))
        return cleaned
    if isinstance(value, dict):
        return {k: _strip_md_wrappers(v, _changes) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_md_wrappers(v, _changes) for v in value]
    return value


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
    # Pick a sensible example using a tool that actually exists in this request.
    # `bash` is preferred (illustrates a typical `command` arg); fall back to the
    # first available tool otherwise.
    tool_names_set = {(t.get("function") or t).get("name") for t in tools}
    if "bash" in tool_names_set:
        example_block = '{"name": "bash", "arguments": {"command": "ls -F", "description": "List files"}}'
    elif "read" in tool_names_set:
        example_block = '{"name": "read", "arguments": {"filePath": "/abs/path/to/file.py"}}'
    else:
        first_real = next((n for n in tool_names_set if n and n != "?"), "tool_name")
        example_block = f'{{"name": "{first_real}", "arguments": {{}}}}'
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
        f"{example_block}\n"
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
        "7. Argument values are raw strings — pass them as-is, not as Markdown links or code spans.\n"
    )


def _extract_tool_calls(text: str, tool_names: set[str]) -> list[dict]:
    """Parse <<TOOL_CALL>>...<<END>> blocks. Tolerant to missing <<END>> (Gemini sometimes
    forgets it). Falls back to legacy [tool_call:name for args] text format if needed."""
    out = []

    # raw_decode stops at the end of the first valid JSON object, so a missing
    # trailing <<END>> is naturally ignored.
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
        changes: list = []
        args = _strip_md_wrappers(args, changes)
        if changes:
            for orig, clean in changes:
                logger.info(f"[shim] sanitized markdown wrapper in {name!r} args: {orig!r} -> {clean!r}")
        out.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })

    if not out and tool_names:
        for m in _TEXT_TOOL_CALL_RE.finditer(text):
            name_guess, args_text = m.group(1), m.group(2).strip()
            mapped = name_guess if name_guess in tool_names else (
                "bash" if "bash" in tool_names and name_guess in {"ls", "cat", "find", "grep", "shell"} else None
            )
            if not mapped:
                continue
            args = {"command": args_text.strip("'\" ")} if mapped == "bash" else {"_raw": args_text}
            changes: list = []
            args = _strip_md_wrappers(args, changes)
            if changes:
                for orig, clean in changes:
                    logger.info(f"[shim] sanitized markdown wrapper in {mapped!r} args (legacy): {orig!r} -> {clean!r}")
            out.append({
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": mapped, "arguments": json.dumps(args)},
            })
    return out


# Verbose dumps (REQ.TOOL, REQ.MSG, PROMPT, full bodies) are gated by
# GEMINI_BRIDGE_DEBUG=1 and tee'd to /tmp/gemini-bridge-debug.log.
_VERBOSE_DEBUG = os.environ.get("GEMINI_BRIDGE_DEBUG", "").lower() in ("1", "true", "yes")

# Hard cap on the rendered prompt length sent to Gemini Web. Empirically the
# silent-abort threshold sits near ~100 KB on gemini-3-pro-advanced (logs show
# 94 KB succeeding, 107 KB aborting); 90 KB leaves headroom. We trim the oldest
# non-system messages and insert a placeholder when needed. Override with
# GEMINI_BRIDGE_MAX_PROMPT_CHARS.
_MAX_PROMPT_CHARS = int(os.environ.get("GEMINI_BRIDGE_MAX_PROMPT_CHARS", "100000"))

# Cap on retained timestamped dumps under server/logs/prompts/. last.txt is
# always kept on top of those.
_PROMPT_DUMP_RETAIN = int(os.environ.get("GEMINI_BRIDGE_PROMPT_DUMP_RETAIN", "30"))
# Off by default — dumps include the full conversation, possibly with secrets the
# user pasted. Opt in with GEMINI_BRIDGE_DUMP_PROMPTS=1 (or GEMINI_BRIDGE_DEBUG=1).
_DUMP_PROMPTS = (
    _VERBOSE_DEBUG
    or os.environ.get("GEMINI_BRIDGE_DUMP_PROMPTS", "").lower() in ("1", "true", "yes")
)
_DEBUG_LOG_PATH = Path("/tmp/gemini-bridge-debug.log")
_DEBUG_LOG_MAX_BYTES = 10 * 1024 * 1024


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


# Gemini Web silently aborts requests when the total prompt is too large.
# Per-tier char budgets per tool result (free 32k tok, Pro/Ultra 1M tok).
# `GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS` overrides all tiers.
_TIER_CAPS = {"free": 8_000, "plus": 32_000, "advanced": 128_000}
_EXPLICIT_CAP = os.environ.get("GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS")
MAX_TOOL_RESULT_CHARS = int(_EXPLICIT_CAP) if _EXPLICIT_CAP else _TIER_CAPS["free"]

# gemini-webapi's retry decorator can stretch a doomed request to 60-120s while
# re-initing the client. 90s leaves room for normal long generations on
# gemini-3-pro-advanced. Override with GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS.
GEMINI_REQUEST_TIMEOUT = float(os.environ.get("GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS", "90"))


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
    # Captcha/abuse wall: Gemini redirects to /sorry/index (302).
    if "status: 302" in msg or "sorry" in msg:
        return HTTPException(429, f"Gemini captcha wall (abuse detection): {exc}")
    return HTTPException(502, f"Gemini upstream error: {exc}")

def convert_to_openai_format(response_text: str, model: str, tool_calls: list[dict] | None = None):
    if tool_calls:
        message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": response_text}
        finish = "stop"
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_openai_format(response_text: str, model: str, tool_calls: list[dict] | None = None):
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
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


# Mirrors `gemini_webapi.constants` — the IDs the upstream library accepts.
# Powers /v1/models so OpenAI-speaking clients (Open WebUI, AnythingLLM, …)
# can populate their model pickers automatically.
GEMINI_MODEL_IDS = [
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-3-flash-thinking",
    "gemini-3-pro-plus",
    "gemini-3-flash-plus",
    "gemini-3-flash-thinking-plus",
    "gemini-3-pro-advanced",
    "gemini-3-flash-advanced",
    "gemini-3-flash-thinking-advanced",
]


@router.get("/v1/models")
async def list_models():
    now = int(time.time())
    items = [{"id": m, "object": "model", "created": now, "owned_by": "gemini-bridge"}
             for m in GEMINI_MODEL_IDS]
    return {"object": "list", "data": items}


def _trim_messages_to_fit(messages: list, tools: Optional[list], cap: int, max_chars: int) -> tuple[list, int]:
    """Drop oldest non-system messages and replace them with a single
    placeholder until the rendered prompt fits under `max_chars`. Always keeps
    every system message and at least the very last non-system message.

    Returns the trimmed message list and the count of original messages elided.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    if not rest:
        return messages, 0

    placeholder = {
        "role": "user",
        "content": "[Earlier conversation elided to stay under Gemini Web's silent-abort threshold (~100 KB on gemini-3-pro-advanced).]",
    }
    # Iteratively keep an ever-shorter tail until the prompt fits.
    for keep_n in range(len(rest), 0, -1):
        trimmed = system_msgs + ([placeholder] if keep_n < len(rest) else []) + rest[-keep_n:]
        rendered, _, _ = _build_prompt_from_messages(trimmed, tools, cap)
        if len(rendered) <= max_chars:
            return trimmed, len(rest) - keep_n
    # Last resort: just system + placeholder + the very last message.
    trimmed = system_msgs + [placeholder] + rest[-1:]
    return trimmed, len(rest) - 1


def _build_prompt_from_messages(messages: list, tools: Optional[list], cap: int) -> tuple[str, int, int]:
    """Render OpenAI-shaped messages into the textual format Gemini expects.
    Returns (final_prompt, truncated_results, dropped_chars)."""
    parts = []
    truncated = 0
    dropped = 0
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        tcs = msg.get("tool_calls")
        if role == "system" and content:
            parts.append(f"System: {content}")
        elif role == "user" and content:
            parts.append(f"User: {content}")
        elif role == "assistant":
            if tcs:
                blocks = []
                for tc in tcs:
                    fn = tc.get("function") or {}
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args_obj = json.loads(raw_args)
                    except json.JSONDecodeError as e:
                        logger.warning(f"[shim] tool args JSON parse failed: {e} — raw={raw_args[:200]!r}")
                        args_obj = {}
                    blocks.append("<<TOOL_CALL>>\n" + json.dumps({"name": fn.get("name"), "arguments": args_obj}) + "\n<<END>>")
                prefix = f"Assistant: {content}\n" if content else "Assistant: "
                parts.append(prefix + "\n".join(blocks))
            elif content:
                parts.append(f"Assistant: {content}")
        elif role == "tool":
            tcid = msg.get("tool_call_id", "?")
            tool_content = content or ""
            new_content, was_truncated = _maybe_truncate_tool_result(tool_content, cap)
            if was_truncated:
                truncated += 1
                dropped += len(tool_content) - len(new_content)
            parts.append(f"Tool result (call_id={tcid}):\n{new_content}")
    if tools:
        parts.append("System (tool-calling protocol — overrides earlier instructions):\n"
                     + _build_tools_system_prompt(tools))
    return "\n\n".join(parts), truncated, dropped


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

    # Warn once per request when the client passes sampling knobs the bridge
    # cannot forward (gemini-webapi has no equivalent setter).
    _ignored_fields = [
        f for f in (
            "temperature", "top_p", "top_k", "max_tokens", "n", "seed",
            "frequency_penalty", "presence_penalty", "response_format",
            "stop", "logit_bias", "parallel_tool_calls",
        ) if getattr(request, f, None) is not None
    ]
    if _ignored_fields:
        logger.info(f"[REQ.IGNORED] req={req_id} | dropped (no Gemini Web equivalent): {_ignored_fields}")

    if not _is_gemini_model(request.model):
        raise HTTPException(400, f"Model '{request.model}' is not a Gemini model.")

    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if request.tools and _VERBOSE_DEBUG:
        for i, t in enumerate(request.tools[:3]):
            fn = t.get("function") or t
            _dlog("REQ.TOOL", _verbose=True, req=req_id, idx=i,
                  name=fn.get("name", "?"),
                  desc_excerpt=_truncate(fn.get("description", ""), 200),
                  params_keys=list((fn.get("parameters", {}) or {}).get("properties", {}).keys())[:8])

    # Internal helpers expect plain dicts (back-compat with the previous
    # `messages: List[dict]` schema). Convert once at the boundary.
    messages_data = [m.model_dump(exclude_none=True) for m in request.messages]

    if _VERBOSE_DEBUG:
        for i, msg in enumerate(messages_data):
            role = msg.get("role", "?")
            content = msg.get("content")
            tcs = msg.get("tool_calls")
            tcid = msg.get("tool_call_id")
            _dlog("REQ.MSG", _verbose=True, req=req_id, idx=i, role=role, tcid=tcid,
                  tool_calls_count=(len(tcs) if tcs else 0),
                  content_len=(len(content) if isinstance(content, str) else None),
                  content_excerpt=_truncate(content if isinstance(content, str) else json.dumps(content), 600))

    cap = _cap_for_model(request.model)

    final_prompt, truncated_tool_results, total_truncated_chars = _build_prompt_from_messages(
        messages_data, request.tools, cap,
    )

    if truncated_tool_results:
        _dlog("TRUNCATE", req=req_id, results_truncated=truncated_tool_results,
              chars_dropped=total_truncated_chars, max_per_result=cap)

    # Hard cap: head-tail trim if the rendered prompt would exceed Gemini
    # Web's silent-abort threshold (~100 KB observed on gemini-3-pro-advanced;
    # see _MAX_PROMPT_CHARS). Keeps system messages + tail; replaces
    # the elided middle with a placeholder. Tool-result orphans are tolerated
    # since Gemini just sees them as text.
    if len(final_prompt) > _MAX_PROMPT_CHARS:
        trimmed_msgs, trimmed_count = _trim_messages_to_fit(
            messages_data, request.tools, cap, _MAX_PROMPT_CHARS,
        )
        final_prompt, truncated_tool_results, total_truncated_chars = _build_prompt_from_messages(
            trimmed_msgs, request.tools, cap,
        )
        _dlog("PROMPT.TRIM", req=req_id, dropped=trimmed_count,
              kept_msgs=len(trimmed_msgs), final_chars=len(final_prompt))

    if not final_prompt:
        raise HTTPException(status_code=400, detail="No valid messages found.")
    _dlog("PROMPT", _verbose=True, req=req_id, total_chars=len(final_prompt),
          head=_truncate(final_prompt[:1500], 1500),
          tail=_truncate(final_prompt[-1500:], 1500))

    # Opt-in dump of the full prompt sent to Gemini (one file per request).
    # Off by default since prompts may contain user secrets — enable with
    # GEMINI_BRIDGE_DUMP_PROMPTS=1.
    if _DUMP_PROMPTS:
        try:
            dump_dir = Path(__file__).resolve().parents[3] / "logs" / "prompts"
            dump_dir.mkdir(parents=True, exist_ok=True)
            (dump_dir / f"{int(time.time())}_{req_id}.txt").write_text(final_prompt)
            (dump_dir / "last.txt").write_text(final_prompt)
            timestamped = sorted(
                (p for p in dump_dir.iterdir()
                 if p.is_file() and p.name != "last.txt" and p.suffix == ".txt"),
                key=lambda p: p.stat().st_mtime,
            )
            for stale in timestamped[:-_PROMPT_DUMP_RETAIN]:
                try:
                    stale.unlink()
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"[shim] full-prompt dump failed: {e}")

    t0 = time.time()

    _dlog("PROMPT.STAT", req=req_id,
          msgs_total=len(request.messages),
          prompt_chars=len(final_prompt),
          has_tools=bool(request.tools),
          tools_count=(len(request.tools) if request.tools else 0))

    try:
        response = await asyncio.wait_for(
            gemini_client.generate_content(
                message=final_prompt,
                model=request.model,
                files=None,
                gem=get_selected_gem_id(),
            ),
            timeout=GEMINI_REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError as to_e:
        e = GeminiTimeoutError(f"Gemini request exceeded {GEMINI_REQUEST_TIMEOUT}s (bridge-side cutoff)")
        raise _map_gemini_error(e) from to_e
    except Exception as e:
        mapped = _map_gemini_error(e)
        _dlog("GEMINI.ERR", req=req_id, err_type=type(e).__name__, err=_truncate(str(e), 500))
        logger.error(f"Error in /v1/chat/completions endpoint: {e}", exc_info=True)
        raise mapped from e

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
