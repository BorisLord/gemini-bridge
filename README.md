# Gemini Bridge

> **Use your Google Gemini Free/Pro/Ultra subscription in OpenCode, Cline, Aider and more like an OpenAI key, except free.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/BorisLord/gemini-bridge?include_prereleases&sort=semver)](https://github.com/BorisLord/gemini-bridge/releases)
[![Tests](https://github.com/BorisLord/gemini-bridge/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/BorisLord/gemini-bridge/actions/workflows/test.yml)
[![GitHub stars](https://img.shields.io/github/stars/BorisLord/gemini-bridge?style=social)](https://github.com/BorisLord/gemini-bridge/stargazers)

Local OpenAI-compatible proxy + Chrome MV3 extension. Any client speaking `/v1/chat/completions` (OpenCode, Cline, Aider, AnythingLLM, Open WebUI, `curl`…) drives **Gemini 3 Pro / Flash / Thinking** through your browser quota — no API key, multi-account ready (`/u/0`, `/u/1`, …), Gems, tool-calls, streaming.

```
Browser cookies ──▶ localhost:6969 ──/v1/chat/completions──▶ OpenCode / Cline / Aider / curl
   (Chrome ext or Firefox/Safari/Chromium auto-discovery)
```

## Install

All paths require a browser signed into `gemini.google.com`. Two ways to feed cookies:
- **Chrome MV3 extension** (Chrome / Edge / Brave / Vivaldi / any Chromium fork) — pushes rotated cookies to the bridge as `extension:<index>`.
- **Browser cookie discovery** (Firefox / LibreWolf / Safari + every Chromium-family browser) — the bridge reads your local browser DB at boot and exposes each session as `<browser>:<index>`.

Both populate the same in-process registry — every chat request routes via `model@<id>` (see [Multi-account](#multi-account)).

**Native** — Linux or macOS. Windows users need WSL (the bridge no longer ships native Windows DPAPI cookie decryption). Requires `git` + [`uv`](https://docs.astral.sh/uv/getting-started/installation/). [`mise`](https://mise.jdx.dev/) users get `uv`/`ruff`/`pip-audit` pinned via `mise.toml` (`mise install` instead).

```bash
git clone https://github.com/BorisLord/gemini-bridge && cd gemini-bridge
./start.sh        # first run sets up venv + deps, then launches on :6969
```

**Docker** — needs Docker ≥ 24. Pre-built image from GHCR (no clone required):

```bash
docker run -d --name gemini-bridge \
  -p 127.0.0.1:6969:6969 \
  -v gemini-bridge-data:/data \
  ghcr.io/borislord/gemini-bridge:latest
```

Or build locally with the bundled compose file (needs the `compose` plugin):

```bash
docker compose up --build -d
```

Both bind to `127.0.0.1:6969` and persist `config.ini` in a named volume.

**Systemd (user service, Linux)** — same prereqs as Native; auto-starts at boot:

```bash
./start.sh --setup-only
mkdir -p ~/.config/systemd/user
cp systemd/gemini-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gemini-bridge
loginctl enable-linger $USER   # start without an active login session
```

Then load the extension:

- **Chrome / Edge / Brave / Vivaldi** — `chrome://extensions/` → *Developer mode* → *Load unpacked* → pick `extension/`.
- **Firefox** (Developer Edition / Nightly / ESR / LibreWolf / Waterfox) — `about:config` → set `xpinstall.signatures.required` to `false` once, then `about:debugging` → *This Firefox* → *Load Temporary Add-on…* → pick `extension/manifest.json`. Firefox Stable is not supported (signature mandatory).

Visit `https://gemini.google.com` once, click the extension icon — status should say **✓ Connected**. Quick check: `curl http://localhost:6969/healthz` → `{"status":"ok"}`.

Update with `git pull && ./start.sh` (Docker: `docker compose up --build -d`; systemd: `systemctl --user restart gemini-bridge`).

## Troubleshooting & logs

- **Popup `× Failed`** → click *Sync now*. If still failing, open `https://gemini.google.com` in a tab to force a cookie rotation, then *Sync now* again.
- **Popup `Server not reachable`** → bridge isn't running. Check `systemctl --user status gemini-bridge` (or `docker compose ps`).
- **Port 6969 already in use** → `lsof -ti:6969 | xargs kill`, or change `GEMINI_BRIDGE_PORT` and update the matching URL in the popup's *Server URL* settings panel.
- **Extension can't load** → reload it from `chrome://extensions/` (toggle off/on). Check the *Service worker* link there for errors.
- **All requests return 502** → cookies likely expired. Browse to `https://gemini.google.com` to refresh, then *Sync now*. Workspace accounts may need admin permission for Gemini.

**Logs**:
- Native / systemd: `server/logs/bridge.log` (rotating, ~100 MB cap), plus `journalctl --user -u gemini-bridge -f` for systemd.
- Docker: `docker compose logs -f gemini-bridge`.
- Verbose mode: `GEMINI_BRIDGE_DEBUG=1` adds full request/response dumps.

## Connect a client

Any client speaking `/v1/chat/completions` works against `http://localhost:6969/v1`. Drop-in configs ship in [`examples/`](examples/):

| Client | Config | Note |
|---|---|---|
| OpenCode | [`opencode.jsonc`](examples/opencode.jsonc) | merge `provider.gemini-web` into `~/.config/opencode/opencode.jsonc` |
| Zed | [`zed.jsonc`](examples/zed.jsonc) | merge into `~/.config/zed/settings.json` |

Model ID suffixes: `-plus` = AI Pro, `-advanced` = AI Ultra, none = Free. Trim entries to match your subscription.

**Got another client working?** PR welcome — drop an `examples/<client>.<ext>` with version tested + quirks in the header.

## Multi-account

The bridge holds an in-process **registry** of every Gemini account it can reach: extension pushes (`extension:N`), browser sessions discovered via `browser_cookie3` (`firefox:N`, `chrome:N`, `brave:N`, …), and an `env:0` slot bootstrapped from `GEMINI_COOKIE_*`. All entries coexist — you can serve N accounts in parallel from a single process and pick one per request.

**Routing is mandatory and explicit.** Every `/v1/chat/completions` request must carry an account id, either via the `X-Bridge-Account` header or via a `model@<id>` suffix. A bare model name returns **400 "Routing required"** — the bridge refuses to guess which Google account a request should be charged to. `GET /v1/models` therefore only ever returns `<model>@<id>` entries.

```bash
# X-Bridge-Account header — terse for curl/scripts
curl -s http://localhost:6969/v1/chat/completions \
  -H 'X-Bridge-Account: firefox:1' \
  -d '{"model":"gemini-3-pro","messages":[{"role":"user","content":"hi"}]}'

# `<model>@<id>` suffix — works in any OpenAI-compat client (OpenCode, Zed, …)
curl -s http://localhost:6969/v1/chat/completions \
  -d '{"model":"gemini-3-pro@firefox:1","messages":[{"role":"user","content":"hi"}]}'
```

**Discovery & inventory**:

```bash
curl -s http://localhost:6969/accounts/        # → [{"id":"firefox:0","email":"…","client_active":false,…}, …]
curl -s -X POST http://localhost:6969/accounts/refresh   # re-run cross-browser discovery (after a fresh login)
curl -s http://localhost:6969/v1/models | jq '.data[].id'  # picker-ready list, every entry routable
```

Lower-level: `gemini_account_index = N` under `[Cookies]` in `server/config.ini` controls which `/u/N` the `env:0` slot binds to.

## Gemini Gems

Open your Gem on `gemini.google.com`, copy the URL (e.g. `https://gemini.google.com/u/0/gem/eb0eb9162487`), paste it (or just the ID) in the popup → **Apply**. Empty + Apply clears. Selection is **per-account** and lives in memory — the popup scopes to whichever account is selected in the picker, and a Gem only resolves on the Google account it was created on.

Scope to a specific account from the CLI — `account_id` is **required** (422 otherwise):

```bash
curl -X POST http://localhost:6969/runtime/gem \
  -H 'Content-Type: application/json' \
  -d '{"gem_id":"eb0eb9162487","account_id":"firefox:1"}'
```

Set `GEMINI_BRIDGE_GEM_ID` to pre-select a Gem on the `env:0` slot at boot.

## Headless / no-extension flow

Drop a `.env` at the repo root (`cp .env.example .env`) — `start.sh` and `docker compose` both auto-source it. Re-paste `__Secure-1PSID` only when you log out (`_1PSIDTS` rotates on its own).

```dotenv
GEMINI_COOKIE_1PSID=g.a000…
GEMINI_COOKIE_1PSIDTS=sidts-…
GEMINI_BRIDGE_ACCOUNT_INDEX=0
GEMINI_BRIDGE_GEM_ID=           # optional
```

## Environment variables

Single source of truth: [`server/src/app/settings.py`](server/src/app/settings.py). Precedence everywhere: **env > `config.ini` > extension/popup runtime**.

| Name | Default | Effect |
|---|---|---|
| `GEMINI_BRIDGE_PORT` | `6969` | Bind port. Update the matching URL in the popup's *Server URL* settings panel. |
| `GEMINI_BRIDGE_ENABLE_DOCS` | unset | `1` exposes Stoplight Elements at `/docs` (schema at `/docs/openapi.json`). Off by default to keep the admin surface invisible. |
| `GEMINI_BRIDGE_DEBUG` | unset | `1` enables verbose logs to console + `/tmp/gemini-bridge-debug.log`. Implies `DUMP_PROMPTS`. |
| `GEMINI_BRIDGE_DUMP_PROMPTS` | unset | `1` writes each rendered prompt to `server/logs/prompts/`. Off by default — prompts may carry user secrets. |
| `GEMINI_BRIDGE_MAX_PROMPT_CHARS` | `100000` | Hard cap on the rendered prompt sent to Gemini Web (silent-abort guardrail). |
| `GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS` | `90` | Request cap forwarded to `gemini-webapi.init(timeout=...)`. Bump for Ultra deep_research / long file analysis. |
| `GEMINI_BRIDGE_ACCOUNT_IDLE_CLOSE_SECONDS` | `1800` | Per-account idle window before `gemini-webapi` closes the lib client (re-opened transparently on next request). |
| `GEMINI_BRIDGE_ACCOUNT_REFRESH_INTERVAL_SECONDS` | `600` | Per-account cadence at which `gemini-webapi` rotates `__Secure-1PSIDTS` in background. |
| `GEMINI_COOKIE_1PSID` / `_1PSIDTS` | from config / browser | Headless cookie auth. |
| `GEMINI_BRIDGE_ACCOUNT_INDEX` | `0` | `/u/N` selection for the `env:0` slot bootstrapped from `GEMINI_COOKIE_*`. |
| `GEMINI_BRIDGE_GEM_ID` | unset | Pre-select a Gem on the `env:0` slot at boot. |

## HTTP API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | none | OpenAI chat. Streaming + tool calls. |
| `GET` | `/v1/models` | none | OpenAI model list (drives picker auto-discovery). |
| `GET` | `/healthz` | none | Health check. |
| `GET` | `/accounts/` | none | List the registry (id, source, email, client_active, selected_gem_id). |
| `POST` | `/accounts/refresh` | none | Re-run cross-browser discovery and upsert any new accounts. |
| `POST` | `/auth/cookies/{provider}` | extension | Push fresh Google cookies (upserts `extension:N`). |
| `POST` | `/auth/accounts/{provider}` | extension | Probe `/u/0…7` for signed-in emails. |
| `GET` | `/runtime/status` | extension | Per-account state (accounts array). |
| `POST` | `/runtime/gem` | extension | Set active Gem (URL or ID). `account_id` is required (422 otherwise). |

"Extension" = `Origin: chrome-extension://…` OR `X-Extension-Id` header. The bridge binds loopback only — it's CSRF hygiene, not authn.

## Tool-result truncation

Each tool-result message is head+tail truncated per tier before reaching Gemini: ~8k chars (Free), ~32k (`-plus`), ~128k (`-advanced`). Override in [`settings.TIER_TOOL_RESULT_CAPS`](server/src/app/settings.py).

## Prompt sizing & head-tail trimming

Gemini Web silently aborts above **~100 KB** rendered prompt (limit drifts per model/session — 94 KB has worked, 107 KB has failed). The bridge caps at 100 KB by default; on overflow it preserves every `role: "system"` message, drops the oldest non-system messages until the prompt fits, and inserts a single elision placeholder. Full history stays on the client (OpenCode resends it next turn) — only the wire prompt is trimmed.

Override with `GEMINI_BRIDGE_MAX_PROMPT_CHARS=NNNNN`.

## Known upstream limitations (`gemini-webapi`)

The bridge depends on [`HanaokaYuzu/Gemini-API`](https://github.com/HanaokaYuzu/Gemini-API). Two upstream issues drive the current design (fully stateless, no `cid/rid/rcid` reuse):

- **[#297](https://github.com/HanaokaYuzu/Gemini-API/issues/297)** — Google removed the `SNlM0e` access token in April 2026, so `_fetch_user_status` logs `UNAUTHENTICATED` at boot. **Chat is unaffected** — `StreamGenerate` accepts plain cookies. Only `client.list_models()` is degraded (falls back to a minimal Free-tier registry); don't gate features on it. [PR #310](https://github.com/HanaokaYuzu/Gemini-API/pull/310) (open) fixes this once merged.
- **[PR #296](https://github.com/HanaokaYuzu/Gemini-API/pull/296)** — `ChatSession.__init__` aliases `DEFAULT_METADATA` instead of copying it; merged on `main` but unreleased. Until a release ships it, instantiating `ChatSession` mutates global state — the bridge therefore avoids it entirely (full-history replay every turn).
- **Chrome device-bound cookies** — Chromium-family cookies are device-bound on 2025+ Chrome, so cookies pulled from Chrome's DB by `browser_cookie3` trigger silent aborts on Pro models. Workaround: use Firefox for the headless cookie path (`browser_cookie3` reads it as plain text), or push via the Chrome MV3 extension (cookies stay in-browser, only the values cross the wire).

## Known limitations

- **Synthetic SSE**: `gemini-webapi` returns the full response in one shot; the bridge chunks it into SSE frames. Protocol-compliant, no typewriter effect.
- **No usage tracking**: `usage` block is always zero (Gemini Web doesn't expose remaining quota).
- **Tool calling via shim**: Gemini Web has no native function calling, so the bridge prompts the model to emit a structured block and parses it into OpenAI `tool_calls[]`. Works with OpenCode (Read/Edit/Bash/WebFetch).
- **Single-worker uvicorn**: the account registry is in-process; one worker per bridge is the supported topology. Scaling axis = multiple Gemini accounts under that single worker (see Multi-account).

## Contributing

Dev: `mise run lint && mise run test` (or `cd server && python -m unittest discover tests -v`). Architecture notes in [`AGENTS.md`](AGENTS.md). Issues and PRs welcome.

If this saves you an API bill, [**leave a star**](https://github.com/BorisLord/gemini-bridge/stargazers) — it's how others find it.

## License

MIT — see [`LICENSE`](LICENSE).
