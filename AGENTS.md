# AGENTS.md

## What this is

OpenAI-compatible Python proxy (FastAPI) that exposes Gemini Web (`gemini.google.com`) on `localhost:6969/v1`. Hard fork of `Amm1rr/WebAI-to-API`. A Chrome MV3 extension pushes Google `__Secure-1PSID*` cookies to the bridge, which forwards them to `gemini-webapi` (HanaokaYuzu/Gemini-API) to talk to Gemini.

## Stack

- Python 3.13 pinned via `mise.toml`
- FastAPI + uvicorn **single-worker only** (see Pitfalls)
- `gemini-webapi>=2.0.0`, `browser-cookie3`, `httpx`, `curl-cffi`
- Tests: stdlib `unittest`
- Tooling: `uv`, `ruff`, `pip-audit` orchestrated by `mise`

## Commands

```
mise run lint       # ruff --select F,B server/src server/tests
mise run test       # unittest discover server/tests -v
mise run audit      # pip-audit -r server/requirements.txt --strict
mise run serve      # ./start.sh on port 6969
mise run setup      # venv + deps via uv pip sync
```

Health check: `curl http://localhost:6969/healthz`.

## Layout

| Area | Role |
|---|---|
| `server/src/run.py` | Entrypoint script: arg parsing, cookie probe, boot banner, `uvicorn.Server.run()` |
| `server/src/app/main.py` | FastAPI bootstrap, lifespan, CORS, OpenAI-shape exception handlers, `/docs` toggle |
| `server/src/app/endpoints/chat.py` | `/v1/chat/completions`, `/v1/models`, prompt building, tool-call shim |
| `server/src/app/endpoints/auth.py` | `/auth/cookies/{provider}`, `/auth/accounts/{provider}`, `/admin/status`, `/admin/gem` |
| `server/src/app/services/gemini_client.py` | `gemini-webapi` wrapper, module-level globals `_gemini_client` + `_selected_gem_id` |
| `server/src/app/utils/browser.py` | Linux/macOS fallback that reads Gemini cookies via `browser_cookie3` (Firefox-family or any Chromium-family browser per `[Browser].name`) |
| `server/src/app/settings.py` | Centralised env-var reading. Add new `GEMINI_BRIDGE_*` knobs here, not inline. |
| `server/src/schemas/request.py` | `OpenAIChatRequest` + typed `ChatMessage` Pydantic models for `/v1/chat/completions` |
| `server/tests/` | stdlib `unittest` suites (chat handler, admin checks, env resolvers, shim) |
| `extension/` | Chrome MV3 — `popup.{html,js}`, `background.js`, `providers.js`, `manifest.json` |
| `extension/icons/icon.svg` | Source of truth — PNG sizes regenerated with `rsvg-convert -w N -h N icon.svg -o iconN.png` |
| `examples/` | Drop-in client configs (`opencode.jsonc`, `anythingllm.env`, `open-webui.env`) |

## Known pitfalls (not derivable from code)

- **Single-worker uvicorn is mandatory** — `_gemini_client` and `_selected_gem_id` are module-level globals in `services/gemini_client.py`. Multi-worker yields disjoint clients.
- **Stateless mode** — `ChatSession` is avoided entirely until upstream `gemini-webapi` releases PR #296 (`DEFAULT_METADATA.copy()`) and resolves issue #297 (missing `SNlM0e` token). Each request rebuilds the full prompt.
- **Silent abort at ~100 KB** — Gemini Web drops prompts above ~100 KB silently (varies per model). Reason for `_trim_messages_to_fit()` + cap `_MAX_PROMPT_CHARS=100000`. Override with env `GEMINI_BRIDGE_MAX_PROMPT_CHARS=N`.
- **Chrome device-bound cookies** (2025+) — cookies extracted from Chrome flagged as detached → silent abort on Pro models. Firefox capture is the workaround.
- **Synthetic SSE** — `gemini-webapi` returns the full response in one shot; the bridge then chunks it into SSE frames.
- **Tool-calling via regex shim** — Gemini Web has no native function calling. The bridge injects a custom system prompt asking Gemini to emit `<<TOOL_CALL>>{...}<<END>>`, then parses it back into OpenAI `tool_calls[]`.

## Working rules

- Before claiming "done", always run `mise run lint && mise run test`.
- When touching OpenAI-compat endpoints (`/v1/*`), test with both a plain `curl` **and** a real client (Chrome extension or `examples/opencode.jsonc`) — Pydantic validation can pass while serialization breaks on the SDK side.
- **Never** restart the running bridge service or kill the process listening on `:6969` without confirmation — a dev instance may be in use.
- All files in this repo must be in English (code, docs, comments, commit messages).
- `/docs`, `/redoc`, `/openapi.json` are off by default. Set `GEMINI_BRIDGE_ENABLE_DOCS=1` to inspect the schema during development.

### Extension policy

The Chrome extension is **permanently developer-mode (Load unpacked)** and will **never be published** to the Chrome Web Store. The following are therefore out of scope and should not be raised in audits:

- Broad `host_permissions` in `manifest.json`
- CORS `chrome-extension://*` not narrowed to a specific ID (changes per dev install)
- `_check_extension()` accepting any non-empty `X-Extension-Id` — loopback bind is the real boundary

These constraints are revisited when the bridge gains remote exposure with real auth.

### Commits

- **Split by logical layer**: one commit per area (services / endpoints / extension / config / docs / tests). Avoid mega-commits. The goal is fine-grained `git bisect` and revert.
- Format: `type(scope): description`. See `git log` for the in-use style.

### Tests

- **Tests are rigid — they don't adapt to code.** When a test fails after a change:
  - First reflex: *did I break a contractual intent?* If yes → fix the code, not the test.
  - **Never** modify an assertion just to make a red test pass. If the contract must legitimately change, make that explicit before touching the test.
- New code = new tests describing the *expected* behavior, not a copy of the *observed* behavior.
- Mocks **only at the boundary** (external HTTP, time, randomness). No mocks on internal services, on disk reads, etc. — those hide wiring bugs.

## Security

The bridge handles Google session cookies (`__Secure-1PSID*`) — treat them like passwords. Defaults are safe (loopback bind, `chmod 0600` on `config.conf`, no telemetry). Full threat model and reporting policy in [`SECURITY.md`](SECURITY.md).

When making security-sensitive changes (auth, CORS, secrets, file permissions, network exposure), update `SECURITY.md` in the same commit.

## Deploy

Three install modes (full instructions in [`README.md`](README.md)):

- **Native** — `./start.sh` (creates venv, installs deps, runs uvicorn on `:6969`).
- **Docker** — `docker compose up -d` (binds `127.0.0.1:6969`, persists `config.conf` to a named volume).
- **systemd user service** — `systemctl --user enable --now gemini-bridge` after `./start.sh --setup-only`.

All modes are loopback-only. Remote exposure is not the current default.

## Quick debug

- **Server logs**: `server/logs/bridge.log` (rotating, ~100 MB cap). Systemd: `journalctl --user -u gemini-bridge -f`. Docker: `docker compose logs -f bridge`.
- **Verbose**: `GEMINI_BRIDGE_DEBUG=1` → full request/response dumps in logs + `/tmp/gemini-bridge-debug.log`.
- **Prompt dumps**: `GEMINI_BRIDGE_DUMP_PROMPTS=1` → one file per request in `server/logs/prompts/<ts>_<reqid>.txt` (gated; may contain user secrets).
- **Cookie state**: `curl -H "X-Extension-Id: dev" http://localhost:6969/admin/status`.
- **Opaque Gemini errors**: check `_map_gemini_error()` in `chat.py` — the upstream lib often returns generic messages that hide a 401 / 429 / captcha wall (302 → `/sorry/index`).
