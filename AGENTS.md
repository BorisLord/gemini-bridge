# AGENTS.md

## What this is

OpenAI-compatible Python proxy (Litestar) that exposes Gemini Web (`gemini.google.com`) on `localhost:6969/v1`. A Chrome MV3 extension pushes Google `__Secure-1PSID*` cookies to the bridge, which forwards them to `gemini-webapi` (HanaokaYuzu/Gemini-API) to talk to Gemini.

## Stack

- Python 3.13 pinned via `mise.toml`
- Litestar 2.x + uvicorn **single-worker only** (see Pitfalls)
- `gemini-webapi>=2.0.0`, `httpx`, `curl-cffi`
- Tests: stdlib `unittest`
- Tooling: `uv`, `ruff`, `pip-audit` orchestrated by `mise`

## Commands

```
mise run lint    # ruff (rules in mise.toml, line-length 120)
mise run test    # unittest discover server/tests -v
mise run audit   # pip-audit --strict
mise run serve   # ./start.sh on :6969
mise run setup   # venv + uv pip sync
```

Health check: `curl http://localhost:6969/healthz`.

## Layout

| Area | Role |
|---|---|
| `server/src/run.py` | Entrypoint: arg parsing, cookie probe, boot banner, `uvicorn.Server.run()` |
| `server/src/app/main.py` | Litestar bootstrap, lifespan, CORS, OpenAI-shape exception handlers |
| `server/src/app/endpoints/chat.py` | `/v1/chat/completions`, `/v1/models`, prompt building, tool-call shim |
| `server/src/app/endpoints/auth.py` | `/auth/cookies/{provider}`, `/auth/accounts/{provider}` — extension cookie push + email probe. |
| `server/src/app/endpoints/runtime.py` | `/runtime/status`, `/runtime/gem` — extension-guarded status + per-account Gem selection. |
| `server/src/app/endpoints/accounts.py` | `/accounts/`, `/accounts/refresh` — registry inventory + cross-browser rediscovery. |
| `server/src/app/services/account_registry.py` | `Account` + `AccountRegistry` (in-process map on `app.state.account_registry`). Ids: `<browser>:<index>`, `extension:<index>`, `env:0`. |
| `server/src/app/services/bootstrap.py` | Boot-time only: env/config resolvers, `parse_gem_id`, `bootstrap_*`. |
| `server/src/app/services/account_routed_client.py` | `AccountRoutedGeminiClient(WebGeminiClient)` — see Pitfalls. |
| `server/src/app/services/account_discovery.py` | `discover_accounts()` — combines browser cookies + `/u/0..7` probes into `[{id, browser, index, email}]`. |
| `server/src/app/utils/browser.py` | Wraps `gemini_webapi.utils.load_browser_cookies`. `[Browser].name` is a tie-break preference. |
| `server/src/app/settings.py` | All `GEMINI_BRIDGE_*` env knobs land here. |
| `server/src/app/schemas/openai_chat.py` | `OpenAIChatRequest` + `ChatMessage` Pydantic models. |
| `server/tests/` | stdlib `unittest` — endpoints, tool-call shim, routing precedence, registry lifecycle. |
| `extension/` | Chrome MV3 — `popup.{html,js}`, `background.js`, `providers.js`, `manifest.json`. |
| `examples/` | Drop-in client configs (`opencode.jsonc`, `zed.jsonc`). |

## Known pitfalls (not derivable from code)

- **Single-worker uvicorn is mandatory** — the `AccountRegistry` lives on `app.state.account_registry` and is per-process. Multi-worker yields disjoint registries (extension push lands on worker A, chat request hits worker B with no client).
- **`AccountRoutedGeminiClient` MUST be a subclass** — the lib's `@running` decorator silently re-inits the client when `auto_close` fires, rebuilding `AsyncSession` from scratch. Overriding `init()` is the only way to re-install the `/u/{N}/` route patch on every re-init. A composition wrapper would lose the patch on the first auto-reopen.
- **Lifecycle is 100% delegated to `gemini-webapi`** — `init(auto_close=True, close_delay=…, auto_refresh=True, refresh_interval=…)` per account: the lib closes idle clients, transparently re-inits via `@running` on the next call, and rotates `__Secure-1PSIDTS` in the background. Knobs live in `app/settings.py` (`ACCOUNT_IDLE_CLOSE_SECONDS`, `ACCOUNT_REFRESH_INTERVAL_SECONDS`). Do not re-implement any of these in bridge code.
- **Cookie persistence is the lib's** — rotated `1PSIDTS` is cached in `/tmp/gemini_webapi/<hash>.json` keyed per `__Secure-1PSID`, so N concurrent accounts in the same process don't collide. Bridge no longer writes rotated cookies to `config.ini`.
- **Routing is mandatory and explicit — no default anywhere** — `/v1/chat/completions` MUST carry `X-Bridge-Account: <id>` header OR `model@<id>` suffix (suffix only triggers if `<id>` contains `:`, so `name@example.com`-style models survive). Header wins. No routing → **400**. `/v1/models` returns only `<model>@<id>` entries. `POST /runtime/gem` requires `account_id` (422 otherwise). Stale `[Cookies].selected_account_id` in `config.ini` from older versions is silently ignored.
- **Stateless mode + benign `UNAUTHENTICATED` boot warning** — both rooted in upstream issue #297 (missing `SNlM0e` token) and PR #296 (unmerged `ChatSession` shared-state fix). The bridge avoids `ChatSession` and rebuilds the full prompt every turn. `_fetch_user_status` logs `Account status: UNAUTHENTICATED` at boot because the lib doesn't compute the `SAPISIDHASH` header — chat is unaffected (`StreamGenerate` accepts plain cookies), only `client.list_models()` is degraded (Free-tier shape). Don't gate features on the model registry.
- **Silent abort at ~100 KB** — Gemini Web drops prompts above ~100 KB silently (varies per model). Reason for `_trim_messages_to_fit()` + cap `settings.MAX_PROMPT_CHARS=100_000`. Override with env `GEMINI_BRIDGE_MAX_PROMPT_CHARS=N`.
- **Chrome device-bound cookies** (2025+) — cookies extracted from Chrome flagged as detached → silent abort on Pro models. Firefox capture is the workaround.
- **Synthetic SSE** — `gemini-webapi` returns the full response in one shot; the bridge then chunks it into SSE frames.
- **Tool-calling via regex shim** — Gemini Web has no native function calling. The bridge injects a custom system prompt asking Gemini to emit `<<TOOL_CALL>>{...}<<END>>`, then parses it back into OpenAI `tool_calls[]`.
- **Request timeout deferred to lib** — `AccountRoutedGeminiClient.init` is called with `timeout=settings.REQUEST_TIMEOUT_SECONDS`. Do not re-wrap `client.generate_content` in `asyncio.wait_for` — it would double-time the request and lose the lib's zombie-stream retry. Override via `GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS=N`.

## Working rules

- Before claiming "done", always run `mise run lint && mise run test`.
- When touching OpenAI-compat endpoints (`/v1/*`), test with both a plain `curl` **and** a real client (Chrome extension or `examples/opencode.jsonc`) — Pydantic validation can pass while serialization breaks on the SDK side.
- **Never** restart the running bridge service or kill the process listening on `:6969` without confirmation — a dev instance may be in use.
- All files in this repo must be in English (code, docs, comments, commit messages).
- OpenAPI is off by default. `GEMINI_BRIDGE_ENABLE_DOCS=1` exposes Stoplight Elements at `/docs` (raw schema at `/docs/openapi.json`). Other Litestar UIs (Swagger, Redoc, …) are intentionally not registered — see `render_plugins=` in `app/main.py`.

### Extension policy

The Chrome extension is **permanently developer-mode (Load unpacked)** — never published to the Web Store. Out-of-scope for audits (revisited if the bridge ever gains remote exposure):

- Broad `host_permissions` in `manifest.json`
- CORS `chrome-extension://*` not narrowed to a specific ID
- `extension_only` Guard accepting any non-empty `X-Extension-Id` — loopback bind is the real boundary

### Commits

- **Split by logical layer**: one commit per area (services / endpoints / extension / config / docs / tests). Avoid mega-commits. The goal is fine-grained `git bisect` and revert.
- Format: `type(scope): description`. See `git log` for the in-use style.

### Tests

- **Tests are rigid.** A red test means the code broke a contract — fix the code, not the assertion. Only touch the assertion if the contract has explicitly changed.
- New code = new tests for the *expected* behavior, not a copy of the *observed* behavior.
- Mocks **only at the boundary** (external HTTP, time, randomness). Never on internal services / disk — they hide wiring bugs.

## Security

Treats Google `__Secure-1PSID*` cookies as passwords. Defaults are safe (loopback bind, `chmod 0600` on `config.ini`, no telemetry). Threat model in [`SECURITY.md`](SECURITY.md) — update it in the same commit for any auth/CORS/secrets/network change.

## Deploy

Three loopback-only modes (details in [`README.md`](README.md)): native (`./start.sh`), Docker (`docker compose up -d`), systemd user service.

## Quick debug

- **Server logs**: `server/logs/bridge.log` (rotating, ~100 MB cap). Systemd: `journalctl --user -u gemini-bridge -f`. Docker: `docker compose logs -f bridge`.
- **Verbose**: `GEMINI_BRIDGE_DEBUG=1` → full request/response dumps in logs + `/tmp/gemini-bridge-debug.log`.
- **Prompt dumps**: `GEMINI_BRIDGE_DUMP_PROMPTS=1` → one file per request in `server/logs/prompts/<ts>_<reqid>.txt` (gated; may contain user secrets).
- **Cookie state**: `curl -H "X-Extension-Id: dev" http://localhost:6969/runtime/status`.
- **Opaque Gemini errors**: check `_map_gemini_error()` in `chat.py` — the upstream lib often returns generic messages that hide a 401 / 429 / captcha wall (302 → `/sorry/index`).
