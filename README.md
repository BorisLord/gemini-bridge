# Gemini Bridge

> **Use your Google Gemini Free/Pro/Ultra subscription in OpenCode, Cline, Aider and more like an OpenAI key, except free.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/BorisLord/gemini-bridge?include_prereleases&sort=semver)](https://github.com/BorisLord/gemini-bridge/releases)
[![Python 3.13](https://img.shields.io/badge/python-3.13-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Litestar 2.21](https://img.shields.io/badge/litestar-2.21-FFC107.svg)](https://litestar.dev/)
[![Tests](https://img.shields.io/badge/tests-85_passing-success.svg)](server/tests/)
[![GitHub stars](https://img.shields.io/github/stars/BorisLord/gemini-bridge?style=social)](https://github.com/BorisLord/gemini-bridge/stargazers)

Local OpenAI-compatible proxy + Chrome MV3 extension. Any client speaking `/v1/chat/completions` (OpenCode, Cline, Aider, AnythingLLM, Open WebUI, `curl`…) drives **Gemini 3 Pro / Flash / Thinking** through your browser quota — no API key, multi-account ready (`/u/0`, `/u/1`, …), Gems, tool-calls, streaming.

```
Chrome ──cookies──▶ localhost:6969 ──/v1/chat/completions──▶ OpenCode / Cline / Aider / curl
```

## Why

You're paying for **Gemini AI Pro / Ultra**, but agentic coding clients (OpenCode, Cline, Aider) only speak OpenAI — so the subscription quota you already pay for stays unused. This bridge maps your browser session to `/v1/chat/completions`: same Gemini models, same quota, no extra API bill.

## Install

All paths require a browser signed into `gemini.google.com`. The Chrome MV3 extension covers Chrome / Edge / Brave / Vivaldi / any Chromium fork; Firefox users go through the headless cookie path (`.env` or `[Browser].name` in `config.conf`, see [Headless / no-extension flow](#headless--no-extension-flow)).

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

Both bind to `127.0.0.1:6969` and persist `config.conf` in a named volume.

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

`__Secure-1PSIDTS` rotates ~daily; the extension auto-pushes new values to the bridge.

## Updating

`git pull && ./start.sh` — rebuilds the venv if dependencies changed. Docker: `docker compose up --build -d` rebuilds only if `Dockerfile` / `requirements.txt` changed. Systemd users: `systemctl --user restart gemini-bridge` after pull.

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

### Propose an example for your client

Only `opencode.jsonc` ships verified. If you've wired the bridge into another client (Open WebUI, AnythingLLM, LibreChat, Continue.dev, Cline, Cursor, …) and validated text + streaming + tool-calls if applicable, **please open a PR adding `examples/<client>.<ext>`**. Include a one-line header comment with: client version tested, the field that points to `http://localhost:6969/v1`, and any quirks (dummy API key required, model-discovery toggle, etc.). Open an issue first if you hit something that doesn't work — we'd rather document the gap than ship a broken example.

## Multi-account

Click the icon → **Detect accounts** — the server probes `/u/0…7` and returns signed-in emails. Pick one; selection persists across restarts. Manual override: `gemini_account_index` under `[Cookies]` in `server/config.conf`.

## Gemini Gems

Open your Gem on `gemini.google.com`, copy the URL (e.g. `https://gemini.google.com/u/0/gem/eb0eb9162487`), paste it (or just the ID) in the popup → **Apply**. Empty + Apply clears. Persists in memory; set `GEMINI_BRIDGE_GEM_ID` to pre-select at boot.

## Headless / no-extension flow

Drop a `.env` at the repo root (`cp .env.example .env`) — `start.sh` and `docker compose` both auto-source it:

```dotenv
GEMINI_COOKIE_1PSID=g.a000…
GEMINI_COOKIE_1PSIDTS=sidts-…
GEMINI_BRIDGE_ACCOUNT_INDEX=0
GEMINI_BRIDGE_GEM_ID=           # optional
```

Re-paste `__Secure-1PSID` only when you log out (rotation of `_1PSIDTS` is automatic, see Install). Alternatives: write the same keys under `[Cookies]` in `server/config.conf`, or export pure env vars (12-factor / k8s).

Smoke test:

```bash
curl -s http://localhost:6969/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemini-3-flash","messages":[{"role":"user","content":"hi"}]}' | jq .
```

## Environment variables

Single source of truth: [`server/src/app/settings.py`](server/src/app/settings.py). Precedence everywhere: **env > `config.conf` > extension/popup runtime**.

| Name | Default | Effect |
|---|---|---|
| `GEMINI_BRIDGE_PORT` | `6969` | Bind port. Must match `extension/providers.js`. |
| `GEMINI_BRIDGE_ENABLE_DOCS` | unset | `1` exposes Stoplight Elements at `/docs` (schema at `/docs/openapi.json`). Off by default to keep the admin surface invisible. |
| `GEMINI_BRIDGE_DEBUG` | unset | `1` enables verbose logs to console + `/tmp/gemini-bridge-debug.log`. Implies `DUMP_PROMPTS`. |
| `GEMINI_BRIDGE_DUMP_PROMPTS` | unset | `1` writes each rendered prompt to `server/logs/prompts/`. Off by default — prompts may carry user secrets. |
| `GEMINI_BRIDGE_MAX_PROMPT_CHARS` | `100000` | Hard cap on the rendered prompt sent to Gemini Web (silent-abort guardrail). |
| `GEMINI_COOKIE_1PSID` / `_1PSIDTS` | from config / browser | Headless cookie auth. |
| `GEMINI_BRIDGE_ACCOUNT_INDEX` | `0` | Multi-account `/u/N` selection. |
| `GEMINI_BRIDGE_GEM_ID` | unset | Pre-select a Gem at boot. |

## HTTP API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | none | OpenAI chat. Streaming + tool calls. |
| `GET` | `/v1/models` | none | OpenAI model list (drives picker auto-discovery). |
| `GET` | `/healthz` | none | Health check. |
| `POST` | `/auth/cookies/{provider}` | extension | Push fresh Google cookies. |
| `POST` | `/auth/accounts/{provider}` | extension | Probe `/u/0…7` for signed-in emails. |
| `GET` | `/runtime/status` | extension | Bridge state. |
| `POST` | `/runtime/gem` | extension | Set active Gem (URL or ID). |

"Extension" = `Origin: chrome-extension://…` OR `X-Extension-Id` header. The bridge binds loopback only — it's CSRF hygiene, not authn.

## Tool-result truncation

Each tool-result message is head+tail truncated before being sent to Gemini, sized for the tier:

| Tier (suffix) | Cap |
|---|---|
| Free (none) | ~8k chars |
| Pro (`-plus`) | ~32k chars |
| Ultra (`-advanced`) | ~128k chars |

Caps live in [`settings.TIER_TOOL_RESULT_CAPS`](server/src/app/settings.py) — edit there if you've measured a different threshold.

## Prompt sizing & head-tail trimming

Gemini Web silently aborts requests where the rendered prompt exceeds **~100 KB** on `gemini-3-pro-advanced` (the limit varies a bit per model and per session — empirically the bridge has logged 94 KB succeeding and 107 KB aborting on the same conversation). The abort surfaces as `APIError: silently aborted by Google`. The bridge therefore caps the rendered prompt at **100 KB** by default. When the cap would be exceeded:

1. Every `role: "system"` message is preserved.
2. The oldest non-system messages are dropped and replaced by a single placeholder: `[Earlier conversation elided to stay under Gemini Web's ~134 KB context window.]`.
3. Iteration stops as soon as the rendered prompt fits.

The full original history stays on the **client side** (OpenCode keeps it locally and resends it on the next turn) — only the wire prompt to Gemini is trimmed.

Override with `GEMINI_BRIDGE_MAX_PROMPT_CHARS=NNNNN` if you have a different empirical threshold.

## Known upstream limitations (`gemini-webapi`)

The bridge depends on [`HanaokaYuzu/Gemini-API`](https://github.com/HanaokaYuzu/Gemini-API). Several upstream issues currently block more advanced features and explain why we run **fully stateless** today (no `cid/rid/rcid` reuse, no per-conversation server-side history):

- **[#297](https://github.com/HanaokaYuzu/Gemini-API/issues/297)** — Google removed the `SNlM0e` access token from the Gemini page HTML in April 2026. `gemini-webapi 2.0.0` initialises with `access_token=None`, account reports `UNAUTHENTICATED`. Multi-turn server-side conversations are degraded; advanced models (`gemini-3-pro-advanced`) are unstable. `gemini-3-flash` text-only still works in *guest mode*.
- **[PR #310](https://github.com/HanaokaYuzu/Gemini-API/pull/310)** — `Add Guest mode, periodic activity warmup, and browser client impersonation`. Closes #297 and #239. Includes a `get_access_token` cache-first fix relevant when the bridge forwards a full cookie jar (`__Secure-1PSIDCC` and friends). Currently **OPEN**. Worth installing if/when needed:
  ```bash
  uv pip install 'git+https://github.com/luuquangvu/Gemini-API.git@enable-guest-mode'
  ```
- **[PR #296 / commit `c10eac9`](https://github.com/HanaokaYuzu/Gemini-API/pull/296)** — fix shared-state bug in `ChatSession.__init__` (`DEFAULT_METADATA` aliased instead of copied). Merged on `main` but **not yet released** — any local code that creates a `ChatSession` instance with `gemini-webapi 2.0.0` will mutate the global metadata and corrupt subsequent stateless requests. The bridge therefore avoids `ChatSession` entirely.
- **Chrome device-bound session cookies** — Google now binds Chrome cookies to the device. Cookies exfiltrated to a backend Python process are recognised as detached and trigger silent aborts on Pro models. The PR #310 author explicitly warns this is *"outside the scope of this PR and will be difficult to fix in the near future"*. Workaround documented upstream: capture cookies from **Firefox** (not device-bound) and use `impersonate="chrome"` + HTTP/3.

### Future re-enable path
A previous `X-Session-Affinity` / `X-Session-Id` header path (forwarding only the per-turn delta to a reused `ChatSession`) was implemented and then reverted. Rewire it once **all of**: PR #296 is released, PR #310 is merged, and a Firefox cookie capture is wired in the extension. Until then, full-history replay + the head-tail trim above is the only reliable mode.

## Known limitations

- **Stateless replay**: each request resends the full history (head-tail trimmed under 120 KB). See *Known upstream limitations* for why server-side `cid/rid/rcid` reuse is on hold.
- **Synthetic SSE**: `gemini-webapi` returns the full response in one shot; bridge chunks it into SSE frames after. No typewriter effect, but protocol-compliant.
- **No usage tracking**: `usage` block is always zero (Gemini Web doesn't expose remaining quota).
- **Tool calling via shim**: Gemini Web has no native function calling, so the bridge prompts the model to emit a structured block and parses it into OpenAI `tool_calls[]`. Works with OpenCode (Read/Edit/Bash/WebFetch).
- One Chrome profile = one bridge. Multiple profiles → multiple ports.

## Repository layout

| Path | What it is |
|---|---|
| `extension/` | Chrome MV3 (cookie sync + popup controls). |
| `server/` | Litestar server (Gemini wrapper). |
| `examples/opencode.jsonc` | Drop-in OpenCode config. |
| `systemd/` | User-service unit. |
| `start.sh` | Setup-on-first-run launcher. |

Tests: `mise run test` (or `cd server && python -m unittest discover tests -v` with the venv activated). Covers the chat handler, tool-call shim, Gem URL parsing, admin origin checks, `/v1/models` discovery. Lint: `mise run lint`. Audit deps: `mise run audit`.

## Support

If this saves you an API bill, [**leave a star**](https://github.com/BorisLord/gemini-bridge/stargazers) — it's how others find it. Issues and PRs welcome.

## License

MIT — Copyright (c) 2026 Boris Lord. Original attribution preserved in [`LICENSE`](LICENSE).
