# Gemini Bridge

Local FastAPI + Chrome extension that exposes your **Google Gemini subscription** as an **OpenAI-compatible API** on `http://localhost:6969/v1`. Any client speaking `/v1/chat/completions` (OpenCode, Cline, Continue, Aider, Codex, `curl`…) drives Gemini 3 Pro / Flash / Thinking through your browser quota — no API key, multi-account ready (`/u/0`, `/u/1`, …). Originally forked from [`Amm1rr/WebAI-to-API`](https://github.com/Amm1rr/WebAI-to-API). Verified end-to-end with OpenCode (agentic tool calling).

```
Chrome ──cookies──▶ localhost:6969 ──/v1/chat/completions──▶ OpenCode / curl / …
                                          │
                                          └─(on Gemini quota/error)─▶ OpenRouter (free)
```

## Install

All paths require a Chromium-based browser signed into `gemini.google.com`.

**Native** (Linux / macOS, WSL on Windows) — needs `git` + [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`). Python 3.10+ is fetched by `uv` automatically.

```bash
git clone https://github.com/<you>/gemini-bridge && cd gemini-bridge
./start.sh        # first run sets up venv + deps, then launches on :6969
```

**Docker** — needs Docker ≥ 24 with the `compose` plugin.

```bash
docker compose up --build -d
```

The container binds to `127.0.0.1:6969` and persists `config.conf` in a named volume.

**Systemd (user service, Linux)** — same prereqs as Native; auto-starts at boot:

```bash
./start.sh --setup-only
mkdir -p ~/.config/systemd/user
cp systemd/gemini-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gemini-bridge
loginctl enable-linger $USER   # start without an active login session
```

Logs: `journalctl --user -u gemini-bridge -f`.

Then in Chrome: `chrome://extensions/` → *Developer mode* → *Load unpacked* → pick `extension/`. Visit `https://gemini.google.com` once, click the extension icon — status should say **✓ Connected**. Quick check: `curl http://localhost:6969/healthz` → `{"status":"ok"}`.

`__Secure-1PSIDTS` rotates ~daily; the extension auto-pushes new values via `chrome.cookies.onChanged`.

## Updating

`git pull && ./start.sh` — `start.sh` detects an incomplete venv (broken `import uvicorn`) and rebuilds it. Docker: `docker compose up --build -d` rebuilds only if `Dockerfile` / `requirements.txt` changed. Systemd users: `systemctl --user restart gemini-bridge` after pull.

## Troubleshooting & logs

- **Popup `× Failed`** → click *Sync now*. If still failing, open `https://gemini.google.com` in a tab to force a cookie rotation, then *Sync now* again.
- **Popup `Server not reachable`** → bridge isn't running. Check `systemctl --user status gemini-bridge` (or `docker compose ps`).
- **Port 6969 already in use** → `lsof -ti:6969 | xargs kill`, or change `GEMINI_BRIDGE_PORT` (and `SERVER_BASE_URL` in `extension/providers.js` to match).
- **Extension can't load** → reload it from `chrome://extensions/` (toggle off/on). Check the *Service worker* link there for errors.
- **All requests return 502** → cookies likely expired. Browse to `https://gemini.google.com` to refresh, then *Sync now*. Workspace accounts may need admin permission for Gemini.

**Logs**:
- Native / systemd: `server/logs/bridge.log` (rotating, ~100 MB cap), plus `journalctl --user -u gemini-bridge -f` for systemd.
- Docker: `docker compose logs -f gemini-bridge`.
- Verbose mode: `GEMINI_BRIDGE_DEBUG=1` adds full request/response dumps.

## Connect to OpenCode

Copy `examples/opencode.jsonc` to `~/.config/opencode/opencode.jsonc` (merge the `provider.gemini-web` block if you already have a config), then `/models` → `gemini-web/gemini-3-pro-plus`.

ID suffixes: `-plus` = AI Pro, `-advanced` = AI Ultra, none = Free. Trim entries to match your subscription. Same pattern works for any client hitting `/v1/chat/completions`.

## Multi-account

Click the icon → **Detect accounts** — the server probes `/u/0…7` and returns signed-in emails. Pick one; selection persists across restarts. Manual override: `gemini_account_index` under `[Cookies]` in `server/config.conf`.

## Gemini Gems

Open your Gem on `gemini.google.com`, copy the URL (e.g. `https://gemini.google.com/u/0/gem/eb0eb9162487`), paste it (or just the ID) in the popup → **Apply**. Empty + Apply clears. Persists in memory; set `GEMINI_BRIDGE_GEM_ID` to pre-select at boot.

> Auto-detection was removed — Google's `LIST_GEMS` RPC returns `PERMISSION_DENIED` on too many accounts to be reliable.

## OpenRouter fallback

When Gemini fails (429 / 401 / 502 / 504), the bridge transparently retries on OpenRouter free models in the same HTTP round-trip — clients see a 200 instead of an error. Toggleable from the popup, no restart.

Get a free key at [openrouter.ai/keys](https://openrouter.ai/keys), then set it via `OPENROUTER_API_KEY=…`, `[OpenRouter] api_key=…` in `config.conf`, or the popup. Defaults: enabled, model `qwen/qwen3-coder:free`. Other curated free picks: `z-ai/glm-4.5-air:free`, `openai/gpt-oss-120b:free`, `meta-llama/llama-3.3-70b-instruct:free`, `nvidia/nemotron-3-super-120b-a12b:free` — all support tool calls.

After one successful fallback, the next ~1h of requests bypass Gemini directly (`reason=sticky`). Reset via the popup *Retry Gemini now* button or `POST /admin/reset-fallback`. Override the window with `GEMINI_BRIDGE_FALLBACK_STICKY_HOURS=<n>` (`0` disables). Pass a non-Gemini model ID to bypass Gemini entirely (passthrough).

Visibility: response header `X-Bridge-Fallback: openrouter:<model>:<reason>`, response `model` field becomes `gemini-3-pro→openrouter:qwen/qwen3-coder:free`. Free-tier daily caps apply (~50 req/day per model); deposit $10 on OpenRouter to raise to ~1000.

## Headless / no-extension flow

Drop a `.env` at the repo root (`cp .env.example .env`) — `start.sh` and `docker compose` both auto-source it:

```dotenv
GEMINI_COOKIE_1PSID=g.a000…
GEMINI_COOKIE_1PSIDTS=sidts-…
GEMINI_BRIDGE_ACCOUNT_INDEX=0
OPENROUTER_API_KEY=sk-or-v1-…   # optional
GEMINI_BRIDGE_GEM_ID=           # optional
```

`__Secure-1PSIDTS` auto-rotates after first use; re-paste `__Secure-1PSID` only when you log out. Alternatives: write the same keys under `[Cookies]` / `[OpenRouter]` in `server/config.conf`, or export pure env vars (12-factor / k8s).

Smoke test:

```bash
curl -s http://localhost:6969/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemini-3-flash","messages":[{"role":"user","content":"hi"}]}' | jq .
```

## Environment variables

Precedence everywhere: **env > `config.conf` > extension/popup runtime**.

| Name | Default | Effect |
|---|---|---|
| `GEMINI_BRIDGE_PORT` | `6969` | Bind port. Must match `extension/providers.js`. |
| `GEMINI_BRIDGE_DEBUG` | unset | `1` enables verbose logs to console + `/tmp/gemini-bridge-debug.log`. |
| `GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS` | `30` | Hard cutoff per Gemini call. |
| `GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS` | tier-adaptive | Override the per-tier cap (8k/32k/128k). |
| `GEMINI_COOKIE_1PSID` / `_1PSIDTS` | from config / browser | Headless cookie auth. |
| `GEMINI_BRIDGE_ACCOUNT_INDEX` | `0` | Multi-account `/u/N` selection. |
| `GEMINI_BRIDGE_GEM_ID` | unset | Pre-select a Gem at boot. |
| `OPENROUTER_API_KEY` | unset | Bearer for fallback. |
| `GEMINI_BRIDGE_FALLBACK_ENABLED` | `true` | Initial toggle. |
| `GEMINI_BRIDGE_FALLBACK_MODEL` | `qwen/qwen3-coder:free` | Initial OpenRouter model. |
| `GEMINI_BRIDGE_FALLBACK_STICKY_HOURS` | `1` | Sticky window after success. `0` disables. |
| `GEMINI_BRIDGE_OPENROUTER_TIMEOUT_SECONDS` | `60` | Hard cutoff per OpenRouter call. |

## HTTP API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | none | OpenAI chat. Streaming + tool calls. |
| `GET` | `/healthz` | none | Liveness probe. |
| `POST` | `/auth/cookies/{provider}` | extension | Push fresh Google cookies. |
| `POST` | `/auth/accounts/{provider}` | extension | Probe `/u/0…7` for signed-in emails. |
| `GET` | `/admin/status` | extension | Bridge state. |
| `POST` | `/admin/reset-fallback` | extension | Clear sticky window. |
| `POST` | `/admin/openrouter` | extension | Update fallback config. |
| `POST` | `/admin/gem` | extension | Set active Gem (URL or ID). |

"Extension" = `Origin: chrome-extension://…` OR `X-Extension-Id` header. The bridge binds loopback only — it's CSRF hygiene, not authn.

## Tool-result truncation

Each tool-result message is head+tail truncated before being sent to Gemini, sized for the tier:

| Tier (suffix) | Cap |
|---|---|
| Free (none) | ~8k chars |
| Pro (`-plus`) | ~32k chars |
| Ultra (`-advanced`) | ~128k chars |

Override globally with `GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS=<n>`.

## Known limitations

- **Synthetic SSE**: `gemini-webapi` returns the full response in one shot; bridge chunks it into SSE frames after. No typewriter effect, but protocol-compliant.
- **No usage tracking**: `usage` block is always zero (Gemini Web doesn't expose remaining quota).
- **Tool calling via shim**: bridge tells Gemini to emit `<<TOOL_CALL>>{json}<<END>>` blocks and parses them into OpenAI `tool_calls[]`. Tested with OpenCode (Read/Edit/Bash/WebFetch). OpenRouter calls use native tool calling — no shim.
- One Chrome profile = one bridge. Multiple profiles → multiple ports.

## Repository layout

| Path | What it is |
|---|---|
| `extension/` | Chrome MV3 (cookie sync + popup controls). |
| `server/` | FastAPI server (Gemini wrapper + OpenRouter fallback). |
| `examples/opencode.jsonc` | Drop-in OpenCode config. |
| `systemd/` | User-service unit. |
| `start.sh` | Setup-on-first-run launcher. |

Tests: `cd server && .venv/bin/python -m unittest discover tests -v`. Covers the tool-call shim, Gem URL parsing, OpenRouter state machine, admin origin checks.

## License

MIT — Copyright (c) 2026. Forked from [`Amm1rr/WebAI-to-API`](https://github.com/Amm1rr/WebAI-to-API) (MIT) — original copyright preserved in `server/LICENSE`.
