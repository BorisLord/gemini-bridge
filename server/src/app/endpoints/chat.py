import base64
import contextlib
import io
import json
import mimetypes
import re
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import httpx
from app import settings
from app.logger import logger
from app.schemas.openai_chat import OpenAIChatRequest, err_response
from app.services.account_registry import Account, AccountRegistry
from gemini_webapi.exceptions import ModelInvalid, TemporarilyBlocked, UsageLimitExceeded
from gemini_webapi.exceptions import TimeoutError as GeminiTimeoutError
from litestar import Controller, Request, get, post
from litestar.enums import MediaType
from litestar.exceptions import HTTPException
from litestar.response import Response, ServerSentEvent
from PIL import Image, UnidentifiedImageError

# gemini-webapi only speaks free-form text → we prompt Gemini to emit a
# delimited JSON block, then parse it back into OpenAI `tool_calls[]`.
_TOOL_CALL_RE = re.compile(r"<<TOOL_CALL>>\s*(\{.*?\})\s*<<END>>", re.DOTALL)
# OpenCode-prompt-leaked text format that some clients still emit.
_TEXT_TOOL_CALL_RE = re.compile(r"\[tool_call:\s*(\w+)\s+for\s+(.+?)\]", re.DOTALL)

# Gemini-3-pro wraps string args in Markdown (`[X](Y)` or `` `X` ``) → breaks
# downstream consumers (e.g. webfetch rejects bracketed URLs).
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
        if target.startswith(("http://", "https://", "/", "./", "../")) or "/" in target:
            return target
        return text
    m = _MD_CODE_RE.match(s)
    if m:
        return m.group(1).strip()
    return s


def _strip_md_wrappers(value: Any, _changes: list | None = None) -> Any:
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
        # Tolerate both `{type:"function", function:{...}}` and flat `{name, ...}`.
        fn = t.get("function") or t
        desc_lines = (fn.get("description", "") or "").splitlines()
        first_line = desc_lines[0][:200] if desc_lines else ""
        rendered.append(
            f"- name: {fn.get('name', '?')}\n"
            f"  description: {first_line}\n"
            f"  parameters (JSON Schema): {json.dumps(fn.get('parameters', {}))[:500]}"
        )
    # Pick an example tool that actually exists this request — `bash` first
    # (typical `command` arg), then `read`, else fall back to the first available.
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
        "Concrete example (replace fields with what you need):\n"
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
    """Parse <<TOOL_CALL>>...<<END>> blocks; falls back to the legacy
    `[tool_call:name for args]` text format. `raw_decode` stops at the end of
    the first valid JSON object, so a missing trailing <<END>> (Gemini sometimes
    forgets it) is naturally tolerated."""
    out = []

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


_DEBUG_LOG_PATH = Path(settings.DEBUG_LOG_PATH)
_PROMPTS_DUMP_DIR = Path(__file__).resolve().parents[3] / "logs" / "prompts"


def _dlog(tag: str, _verbose: bool = False, **fields) -> None:
    """Structured log. _verbose=True entries are dropped unless GEMINI_BRIDGE_DEBUG=1."""
    if _verbose and not settings.DEBUG:
        return
    line = f"[{tag}] " + " | ".join(f"{k}={v!r}" if not isinstance(v, str) else f"{k}={v}" for k, v in fields.items())
    logger.info(line)
    if not settings.DEBUG:
        return
    # Best-effort debug-log write. TOCTOU on rotation is benign under the
    # single-worker contract; suppress so debug noise never breaks a request.
    with contextlib.suppress(Exception):
        if _DEBUG_LOG_PATH.exists() and _DEBUG_LOG_PATH.stat().st_size > settings.DEBUG_LOG_MAX_BYTES:
            _DEBUG_LOG_PATH.rename(_DEBUG_LOG_PATH.with_suffix(".log.1"))
        with _DEBUG_LOG_PATH.open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")


def _truncate(s: str | None, n: int = 800) -> str:
    if s is None:
        return "<None>"
    if len(s) <= n:
        return s
    return f"{s[:n//2]}…[TRUNCATED {len(s)-n} chars]…{s[-n//2:]}"


# Per-tier char budgets per tool result (free 32k tok, Pro/Ultra 1M tok).
MAX_TOOL_RESULT_CHARS = settings.TIER_TOOL_RESULT_CAPS["free"]


def _cap_for_model(model: str) -> int:
    if model.endswith("-advanced"):
        return settings.TIER_TOOL_RESULT_CAPS["advanced"]
    if model.endswith("-plus"):
        return settings.TIER_TOOL_RESULT_CAPS["plus"]
    return settings.TIER_TOOL_RESULT_CAPS["free"]


def _maybe_truncate_tool_result(content: str, cap: int = MAX_TOOL_RESULT_CHARS) -> tuple[str, bool]:
    if not isinstance(content, str) or len(content) <= cap:
        return content, False
    head = cap // 2
    tail = cap - head - 80
    return (
        content[:head]
        + f"\n\n…[truncated {len(content) - cap} chars to stay under upstream prompt limits]…\n\n"
        + content[-tail:],
        True,
    )


def _map_gemini_error(exc: Exception) -> HTTPException:
    """Typed `gemini_webapi.exceptions` first (reliable), string-scan fallback for
    cases the lib leaves as a generic `APIError("status: NNN ...")` — the upstream
    lib doesn't always promote HTTP status codes to typed classes."""
    if isinstance(exc, GeminiTimeoutError):
        return HTTPException(status_code=504, detail=f"Gemini upstream timed out: {exc}")
    if isinstance(exc, UsageLimitExceeded):
        return HTTPException(status_code=429, detail=f"Gemini usage limit reached: {exc}")
    if isinstance(exc, TemporarilyBlocked):
        return HTTPException(status_code=429, detail=f"Gemini captcha wall (abuse detection): {exc}")
    if isinstance(exc, ModelInvalid):
        return HTTPException(status_code=400, detail=f"Gemini rejected the model: {exc}")
    msg = str(exc).lower()
    if "status: 401" in msg or "status: 403" in msg:
        return HTTPException(status_code=401, detail=f"Gemini auth refused: {exc}")
    # Captcha/abuse wall: Gemini redirects to /sorry/index (302).
    if "status: 302" in msg or "sorry" in msg:
        return HTTPException(status_code=429, detail=f"Gemini captcha wall (abuse detection): {exc}")
    if "status: 429" in msg or "quota" in msg or "rate limit" in msg:
        return HTTPException(status_code=429, detail=f"Gemini usage limit reached: {exc}")
    return HTTPException(status_code=502, detail=f"Gemini upstream error: {exc}")


def convert_to_openai_format(
    response_text: str,
    model: str,
    tool_calls: list[dict] | None = None,
    reasoning: str | None = None,
) -> dict:
    if tool_calls:
        message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": response_text}
        finish = "stop"
    if reasoning:
        message["reasoning_content"] = reasoning
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_openai_format(
    response_text: str,
    model: str,
    tool_calls: list[dict] | None = None,
    reasoning: str | None = None,
) -> Iterator[str]:
    """Yield raw JSON payloads (and the OpenAI sentinel `[DONE]`).
    `ServerSentEvent` wraps each into `data: <payload>\\n\\n`."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    first = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
    }
    yield json.dumps(first)

    if reasoning:
        # DeepSeek-R1 convention adopted by Cline/opencode/Continue: surface the
        # thinking trace in its own delta field so the UI can fold it.
        reasoning_chunk = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"reasoning_content": reasoning}, "finish_reason": None}],
        }
        yield json.dumps(reasoning_chunk)

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
        yield json.dumps(chunk)
        end = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
        }
        yield json.dumps(end)
        yield "[DONE]"
        return

    content = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": response_text}, "finish_reason": None}]
    }
    yield json.dumps(content)
    end = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    }
    yield json.dumps(end)
    yield "[DONE]"


def _is_gemini_model(name: str) -> bool:
    return name.startswith("gemini-")


def _split_model_routing(model: str) -> tuple[str, str | None]:
    """Strip a `@<account_id>` suffix from a model name. Returns (clean_model,
    account_id_or_None). Account ids contain a `:` (e.g. `firefox:0`), so we
    only treat the part after the LAST `@` as a routing token if it matches."""
    if "@" not in model:
        return model, None
    clean, _, account = model.rpartition("@")
    account = account.strip()
    # Reject obvious non-routing suffixes (no colon = not a stable id format).
    if ":" not in account:
        return model, None
    return clean.strip(), account


def _resolve_account(request: Request, model: str) -> tuple[str, Account]:
    """Explicit routing only: header > `model@<id>` suffix. No bare-model fallback
    so quotas can never be charged to the wrong Google account by accident.
    Returns (clean_model, account). 400 if no routing, 404 if id unknown."""
    reg: AccountRegistry = request.app.state.account_registry
    header = request.headers.get("X-Bridge-Account")
    if header and header.strip():
        account_id = header.strip()
        clean = model
    else:
        clean, account_id = _split_model_routing(model)
    if account_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Routing required. Pass `X-Bridge-Account: <id>` header or "
                f"use `<model>@<id>` (e.g. `{model}@firefox:0`). "
                f"Available accounts: {sorted(a.id for a in reg.list()) or '(empty)'}."
            ),
        )
    account = reg.get(account_id)
    if account is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown account_id {account_id!r}. Available: {sorted(a.id for a in reg.list())}",
        )
    return clean, account


# Mirrors `gemini_webapi.constants` — feeds /v1/models for client auto-discovery.
# Order matters: clients that auto-register from this endpoint (e.g. examples/pi.ts)
# pick the first entry as the default model, so the strongest tier comes first.
GEMINI_MODEL_IDS = [
    "gemini-3-pro-advanced",
    "gemini-3-flash-advanced",
    "gemini-3-flash-thinking-advanced",
    "gemini-3-pro-plus",
    "gemini-3-flash-plus",
    "gemini-3-flash-thinking-plus",
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-3-flash-thinking",
]


def _trim_messages_to_fit(messages: list, tools: list | None, cap: int, max_chars: int) -> tuple[list, int]:
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
        "content": (
            "[Earlier conversation elided to stay under Gemini Web's silent-abort "
            "threshold (~100 KB on gemini-3-pro-advanced).]"
        ),
    }
    for keep_n in range(len(rest), 0, -1):
        trimmed = system_msgs + ([placeholder] if keep_n < len(rest) else []) + rest[-keep_n:]
        rendered, _, _ = _build_prompt_from_messages(trimmed, tools, cap)
        if len(rendered) <= max_chars:
            return trimmed, len(rest) - keep_n
    trimmed = [*system_msgs, placeholder, *rest[-1:]]
    return trimmed, len(rest) - 1


def _coerce_text_content(content: Any) -> str:
    """Return only the textual portion of an OpenAI `content` field. Strings pass
    through; multimodal arrays are reduced to their `{type: "text"}` blocks
    (image_url blocks are routed to `files=` separately, not to the prompt)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            (b.get("text") or "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


_DATA_URL_MIME_RE = re.compile(r"^data:([^;,]+)(?:;[^,]*)?,", re.IGNORECASE)


def _ext_for_mime(mime: str) -> str:
    """Map a MIME type back to a file extension. `gemini-webapi.upload_file`
    only inspects the filename to set the upload's Content-Type — bytes-only
    inputs get a `.txt` default that makes Google silently abort image
    requests, so we round-trip through `mimetypes` to keep the right suffix."""
    return mimetypes.guess_extension(mime) or ".bin"


def _maybe_resize_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Downscale + recompress images above Gemini Web's silent-abort
    threshold (~150 KB binary): flatten alpha, try PNG at decreasing dims
    (lossless on text), fall back to JPEG q=95→45 when PNG won't fit.
    Pass-through on Pillow decode failure (e.g. webp animation)."""
    if not mime.startswith("image/") or len(data) <= settings.MAX_IMAGE_BYTES:
        return data, mime
    try:
        orig = Image.open(io.BytesIO(data))
        orig.load()
    except (UnidentifiedImageError, OSError):
        return data, mime
    if orig.mode in ("RGBA", "LA") or (orig.mode == "P" and "transparency" in orig.info):
        rgba = orig.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[-1])
        orig = flat
    elif orig.mode != "RGB":
        orig = orig.convert("RGB")
    last_buf: io.BytesIO | None = None
    last_mime = "image/jpeg"
    for max_dim in (settings.MAX_IMAGE_DIM, 1024, 768, 512):
        img = orig.copy()
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        # PNG first — lossless on text. If it fits, ship it.
        png_buf = io.BytesIO()
        img.save(png_buf, "PNG", optimize=True)
        if len(png_buf.getvalue()) <= settings.MAX_IMAGE_BYTES:
            return png_buf.getvalue(), "image/png"
        for quality in (95, 85, 75, 65, 55, 45):
            jpg_buf = io.BytesIO()
            img.save(jpg_buf, "JPEG", quality=quality, optimize=True, progressive=True)
            last_buf = jpg_buf
            if len(jpg_buf.getvalue()) <= settings.MAX_IMAGE_BYTES:
                return jpg_buf.getvalue(), "image/jpeg"
    return last_buf.getvalue(), last_mime


async def _extract_files_from_messages(messages: list) -> list[Path]:
    """Materialize `image_url` blocks from OpenAI multimodal content into
    tempfiles with the right extension, in declaration order. Returns a list
    of paths — caller is responsible for unlink (try/finally)."""
    paths: list[Path] = []
    try:
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "image_url":
                    continue
                url = (block.get("image_url") or {}).get("url")
                if not isinstance(url, str) or not url:
                    raise HTTPException(status_code=400, detail="image_url block missing 'url' field")
                if url.startswith("data:"):
                    m = _DATA_URL_MIME_RE.match(url)
                    mime = (m.group(1) if m else "application/octet-stream").strip().lower()
                    _, _, b64 = url.partition(",")
                    try:
                        data = base64.b64decode(b64, validate=False)
                    except Exception as e:
                        raise HTTPException(status_code=400, detail=f"Invalid data URL: {e}") from e
                    data, mime = _maybe_resize_image(data, mime)
                    paths.append(_write_tempfile(data, _ext_for_mime(mime)))
                elif url.startswith(("http://", "https://")):
                    # follow_redirects=False — a redirect to 127.0.0.1/localhost
                    # would let an attacker probe loopback services through the
                    # bridge. Loopback-bind is the real defense; this closes
                    # the SSRF angle for any future remote-exposure mode.
                    try:
                        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http:
                            r = await http.get(url)
                            r.raise_for_status()
                    except Exception as e:
                        raise HTTPException(
                            status_code=400, detail=f"Failed to download image_url {url!r}: {e}",
                        ) from e
                    mime = (r.headers.get("content-type") or "application/octet-stream").split(";")[0].strip().lower()
                    data, mime = _maybe_resize_image(r.content, mime)
                    paths.append(_write_tempfile(data, _ext_for_mime(mime)))
                else:
                    raise HTTPException(status_code=400, detail=f"Unsupported image_url scheme: {url!r}")
    except Exception:
        for p in paths:
            with contextlib.suppress(OSError):
                p.unlink()
        raise
    return paths


def _write_tempfile(data: bytes, suffix: str) -> Path:
    f = tempfile.NamedTemporaryFile(  # noqa: SIM115 — we manage lifecycle in the call site
        prefix="gemini-bridge-img-", suffix=suffix, delete=False,
    )
    try:
        f.write(data)
    finally:
        f.close()
    return Path(f.name)


def _build_prompt_from_messages(messages: list, tools: list | None, cap: int) -> tuple[str, int, int]:
    """Render OpenAI-shaped messages into the textual format Gemini expects.
    Returns (final_prompt, truncated_results, dropped_chars)."""
    parts = []
    truncated = 0
    dropped = 0
    for msg in messages:
        role = msg.get("role", "user")
        content = _coerce_text_content(msg.get("content", ""))
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
                    payload = json.dumps({"name": fn.get("name"), "arguments": args_obj})
                    blocks.append(f"<<TOOL_CALL>>\n{payload}\n<<END>>")
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


class ChatController(Controller):
    path = "/v1"
    tags: ClassVar = ["chat"]

    @get("/models", summary="List Gemini models exposed via the bridge")
    async def list_models(self, request: Request) -> dict:
        """Suffixed variants only (`<model>@<id>`) — bare names would 400 on chat."""
        now = int(time.time())
        reg: AccountRegistry = request.app.state.account_registry
        items: list[dict] = [
            {"id": f"{m}@{a.id}", "object": "model", "created": now, "owned_by": "gemini-bridge"}
            for m in GEMINI_MODEL_IDS
            for a in reg.list()
        ]
        return {"object": "list", "data": items}

    @post(
        "/chat/completions",
        status_code=200,
        summary="OpenAI-compatible chat completion (SSE if stream=true)",
        # SSE default: Litestar's `to_asgi_response()` route media_type wins over
        # the response object's own. Non-stream replies override via JSON `Response`.
        media_type="text/event-stream",
        responses={
            400: err_response("Missing/invalid messages, model field, or non-Gemini model."),
            429: err_response("Gemini captcha wall or rate-limited (302 → /sorry/index)."),
            502: err_response("Upstream Gemini Web error."),
            503: err_response("Bridge not initialized — push cookies via /auth/cookies/gemini."),
        },
    )
    async def chat_completions(
        self, data: OpenAIChatRequest, request: Request,
    ) -> Response | ServerSentEvent:
        is_stream = data.stream if data.stream is not None else False

        if not data.messages:
            raise HTTPException(status_code=400, detail="No messages provided.")
        if not data.model:
            raise HTTPException(status_code=400, detail="Model not specified in the request.")

        clean_model, account = _resolve_account(request, data.model)

        req_id = uuid.uuid4().hex[:8]
        _dlog("REQ.HEAD", req=req_id, account_id=account.id, model=clean_model, stream=is_stream,
              msgs=len(data.messages),
              tools=(len(data.tools) if data.tools else 0),
              tool_choice=str(data.tool_choice) if data.tool_choice else None)

        # Warn once per request when the client passes sampling knobs the bridge
        # cannot forward (gemini-webapi has no equivalent setter).
        _ignored_fields = [
            f for f in (
                "temperature", "top_p", "top_k", "max_tokens", "n", "seed",
                "frequency_penalty", "presence_penalty", "response_format",
                "stop", "logit_bias", "parallel_tool_calls",
            ) if getattr(data, f, None) is not None
        ]
        if _ignored_fields:
            logger.info(
                f"[REQ.IGNORED] req={req_id} account_id={account.id} | "
                f"dropped (no Gemini Web equivalent): {_ignored_fields}"
            )

        if not _is_gemini_model(clean_model):
            raise HTTPException(status_code=400, detail=f"Model '{clean_model}' is not a Gemini model.")

        # The lib's @running decorator transparently re-inits later if auto_close fires.
        reg: AccountRegistry = request.app.state.account_registry
        try:
            client = await reg.get_or_init_client(account.id)
        except Exception as e:
            logger.error(f"[registry] init failed for account_id={account.id!r}: {e}", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail=f"Failed to initialize Gemini client for account_id={account.id}: {e}",
            ) from e

        if data.tools and settings.DEBUG:
            for i, t in enumerate(data.tools[:3]):
                fn = t.get("function") or t
                _dlog("REQ.TOOL", _verbose=True, req=req_id, idx=i,
                      name=fn.get("name", "?"),
                      desc_excerpt=_truncate(fn.get("description", ""), 200),
                      params_keys=list((fn.get("parameters", {}) or {}).get("properties", {}).keys())[:8])

        # Internal helpers expect plain dicts; convert once at the boundary.
        messages_data = [m.model_dump(exclude_none=True) for m in data.messages]

        if settings.DEBUG:
            for i, msg in enumerate(messages_data):
                role = msg.get("role", "?")
                content = msg.get("content")
                tcs = msg.get("tool_calls")
                tcid = msg.get("tool_call_id")
                _dlog("REQ.MSG", _verbose=True, req=req_id, idx=i, role=role, tcid=tcid,
                      tool_calls_count=(len(tcs) if tcs else 0),
                      content_len=(len(content) if isinstance(content, str) else None),
                      content_excerpt=_truncate(content if isinstance(content, str) else json.dumps(content), 600))

        cap = _cap_for_model(clean_model)

        final_prompt, truncated_tool_results, total_truncated_chars = _build_prompt_from_messages(
            messages_data, data.tools, cap,
        )

        if truncated_tool_results:
            _dlog("TRUNCATE", req=req_id, results_truncated=truncated_tool_results,
                  chars_dropped=total_truncated_chars, max_per_result=cap)

        # Avoid Gemini Web's silent-abort: trim oldest non-system messages, leave a placeholder.
        # Reassigning `messages_data` here keeps file extraction below in lockstep with the
        # trimmed history — otherwise a dropped older message would still upload its image
        # while its textual context has been elided.
        if len(final_prompt) > settings.MAX_PROMPT_CHARS:
            messages_data, trimmed_count = _trim_messages_to_fit(
                messages_data, data.tools, cap, settings.MAX_PROMPT_CHARS,
            )
            final_prompt, truncated_tool_results, total_truncated_chars = _build_prompt_from_messages(
                messages_data, data.tools, cap,
            )
            _dlog("PROMPT.TRIM", req=req_id, dropped=trimmed_count,
                  kept_msgs=len(messages_data), final_chars=len(final_prompt))

        files_paths = await _extract_files_from_messages(messages_data)
        # Wrap everything from here to the response in a single finally so a tempfile
        # leak is impossible regardless of which branch raises (HTTP 400, dump-prompt
        # failure, generate_content error, etc.). gemini-webapi reads + uploads files
        # synchronously inside generate_content, so unlinking before the SSE generator
        # is consumed is safe (the synthetic stream replays cached text).
        try:
            if files_paths:
                _dlog("REQ.FILES", req=req_id, count=len(files_paths),
                      paths=[str(p) for p in files_paths],
                      total_bytes=sum(p.stat().st_size for p in files_paths))

            if not final_prompt and not files_paths:
                raise HTTPException(status_code=400, detail="No valid messages found.")
            _dlog("PROMPT", _verbose=True, req=req_id, total_chars=len(final_prompt),
                  head=_truncate(final_prompt[:1500], 1500),
                  tail=_truncate(final_prompt[-1500:], 1500))

            if settings.DUMP_PROMPTS:
                try:
                    _PROMPTS_DUMP_DIR.mkdir(parents=True, exist_ok=True)
                    (_PROMPTS_DUMP_DIR / f"{int(time.time())}_{req_id}.txt").write_text(final_prompt)
                    (_PROMPTS_DUMP_DIR / "last.txt").write_text(final_prompt)
                    timestamped = sorted(
                        (p for p in _PROMPTS_DUMP_DIR.iterdir()
                         if p.is_file() and p.name != "last.txt" and p.suffix == ".txt"),
                        key=lambda p: p.stat().st_mtime,
                    )
                    for stale in timestamped[:-settings.PROMPT_DUMP_RETAIN]:
                        with contextlib.suppress(OSError):
                            stale.unlink()
                except Exception as e:
                    logger.warning(f"[shim] full-prompt dump failed: {e}")

            t0 = time.time()

            _dlog("PROMPT.STAT", req=req_id,
                  msgs_total=len(data.messages),
                  prompt_chars=len(final_prompt),
                  has_tools=bool(data.tools),
                  tools_count=(len(data.tools) if data.tools else 0))

            try:
                response = await client.generate_content(
                    final_prompt,
                    model=clean_model,
                    files=[str(p) for p in files_paths] or None,
                    gem=account.selected_gem_id,
                )
            except Exception as e:
                mapped = _map_gemini_error(e)
                _dlog("GEMINI.ERR", req=req_id, account_id=account.id,
                      err_type=type(e).__name__, err=_truncate(str(e), 500))
                logger.error(
                    f"Error in /v1/chat/completions endpoint (account_id={account.id}): {e}",
                    exc_info=True,
                )
                raise mapped from e

            raw_text = response.text or ""
            raw_thoughts = response.thoughts or ""
            _dlog("GEMINI.OK", req=req_id, account_id=account.id,
                  latency_s=round(time.time() - t0, 2), resp_chars=len(raw_text),
                  thoughts_chars=len(raw_thoughts))
            _dlog("GEMINI.BODY", _verbose=True, req=req_id, resp_full=_truncate(raw_text, 2500))

            tool_names: set[str] = set()
            if data.tools:
                for t in data.tools:
                    fn = t.get("function") or t
                    n = fn.get("name")
                    if n:
                        tool_names.add(n)
            tool_calls = _extract_tool_calls(raw_text, tool_names) if data.tools else []
            final_text = _TOOL_CALL_RE.sub("", _TEXT_TOOL_CALL_RE.sub("", raw_text)).strip() if tool_calls else raw_text
            _dlog("SHIM", req=req_id, tool_calls_extracted=len(tool_calls),
                  first_call=(json.dumps(tool_calls[0]) if tool_calls else None),
                  fallback_used=bool(tool_calls and not _TOOL_CALL_RE.search(raw_text)))

            if is_stream:
                _dlog("RESP.STREAM", req=req_id, account_id=account.id,
                      mode="sse", with_tool_calls=bool(tool_calls),
                      with_reasoning=bool(raw_thoughts))
                # Echo `data.model` (suffixed) — OpenCode pickers compare the response model.
                return ServerSentEvent(
                    stream_openai_format(
                        final_text, data.model, tool_calls or None, reasoning=raw_thoughts or None,
                    ),
                )
            _dlog("RESP.JSON", req=req_id, account_id=account.id,
                  mode="json", with_tool_calls=bool(tool_calls),
                  with_reasoning=bool(raw_thoughts))
            return Response(
                content=convert_to_openai_format(
                    final_text, data.model, tool_calls or None, reasoning=raw_thoughts or None,
                ),
                media_type=MediaType.JSON,
            )
        finally:
            for p in files_paths:
                with contextlib.suppress(OSError):
                    p.unlink()
