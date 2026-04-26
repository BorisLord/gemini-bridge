# Gemini Bridge — Easily use your Google Pro subscription in OpenCode

A self-hosted local server (FastAPI + Chrome extension) built on [`Amm1rr/WebAI-to-API`](https://github.com/Amm1rr/WebAI-to-API) that exposes your **Google Gemini subscription** as an **OpenAI-compatible API** on `http://localhost:6969/v1`. The extension auto-syncs your `gemini.google.com` cookies so any client speaking `/v1/chat/completions` (OpenCode, Cline, Continue, Aider, Codex, Roo Code, `curl`…) drives Gemini 3 Pro / Flash / Thinking through your browser quota, no API key needed. Multi-account ready (`/u/0`, `/u/1`, …). Only OpenCode is verified end-to-end with agentic tool-calling loops.

```
Chrome ──cookies──▶ localhost:6969 ──/v1/chat/completions──▶ OpenCode / curl / any OpenAI-compatible client
```

## Requirements

**Common to both install paths** :

- A Chromium-based browser (Chrome, Brave, Edge, …) signed into `gemini.google.com`
- A Google account with an active Gemini subscription (free / Pro / Ultra)

**Native install** (`./start.sh`) :

- `git`
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Python ≥ 3.10 (uv will use 3.13 by default)
- Linux or macOS shell. Windows users should run via WSL.

**Docker install** :

- Docker ≥ 24
- `docker compose` plugin (modern Docker Desktop ships with it)

## Quick start (native)

```bash
git clone https://github.com/<you>/gemini-bridge
cd gemini-bridge
./start.sh        # first run: setup (venv + deps) then launches server on :6969
```

`start.sh` handles first-run setup automatically (asks if you want the optional `g4f` fallback, creates the venv, installs deps). Subsequent runs just start the server. If you want setup-only without launching (CI, `systemd ExecStartPre`, …), pass `--setup-only` : `./start.sh --setup-only`.

Then in Chrome:

1. `chrome://extensions/` → toggle **Developer mode** (top right) → **Load unpacked** → pick `extension/`
2. Visit `https://gemini.google.com` once (so cookies are fresh)
3. Click the Gemini Bridge icon → status should say **✓ Connected**

That's it. The server is now driving Gemini on your behalf.

> **Tip — keeping cookies fresh** : `__Secure-1PSIDTS` rotates roughly every 24h. The extension listens to `chrome.cookies.onChanged` and auto-pushes the new value as soon as Chrome receives it (which happens whenever any logged-in Google domain — Gmail, Drive, YouTube — refreshes in any tab). If the popup ever shows a stale status, just open `https://gemini.google.com` in a tab to force an immediate rotation, and the extension will push the fresh cookies right after.

## Run with Docker

```bash
git clone https://github.com/<you>/gemini-bridge
cd gemini-bridge
docker compose up --build -d
```

The container binds to `127.0.0.1:6969` (loopback only) and persists `config.conf` in a named volume (`gemini-bridge-data`) so cookies and the account index survive restarts. The Chrome extension still pushes to `http://localhost:6969` unchanged — load it as described in *Quick start (native)*, step "Then in Chrome".

To enable the optional g4f fallback in the image :

```bash
WITH_G4F=1 docker compose build && docker compose up -d
```

Logs : `docker compose logs -f gemini-bridge`. Stop : `docker compose down` (data volume is preserved). To wipe cookies : `docker compose down -v`.

> **Note** : a separate `server/Dockerfile` exists inside the vendored `server/` tree — it comes from upstream `Amm1rr/WebAI-to-API` and is **not** what `docker compose` uses. The root `Dockerfile` and `docker-compose.yml` are the supported entry points.

## Connect to OpenCode

Copy `examples/opencode.jsonc` to `~/.config/opencode/opencode.jsonc` (or merge the `provider.gemini-web` block if you already have a config), then in OpenCode: `/models` → pick `gemini-web/gemini-3-pro-plus`.

**Model tiers** : `-plus` suffix = Pro tier, `-advanced` = Ultra tier, no suffix = free tier. Add/remove entries in the `models` block to match what your Google account has access to. Full list the server actually accepts : `curl localhost:6969/v1/models`.

For Cline / Continue / Aider / Codex / `curl`, the same pattern applies — anything that speaks `/v1/chat/completions` against `http://localhost:6969/v1` works.

## Multi-account selection

Signed into several Google accounts in Chrome (perso + work + …) ? By default the bridge uses the first account (`/u/0`). To target another:

1. Click the Gemini Bridge icon
2. Click **Detect accounts** — the server probes `/u/0` … `/u/7` and returns the list of signed-in emails
3. Pick one in the dropdown — the bridge starts routing every Gemini request to `gemini.google.com/u/{N}/…`

Selection persists across Chrome / server restarts. Manual override : set `gemini_account_index = 1` under `[Cookies]` in `server/config.conf`.

## Run as a system service (Linux)

```bash
mkdir -p ~/.config/systemd/user
cp systemd/gemini-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gemini-bridge
loginctl enable-linger $USER   # start at boot, even without an active login session
```

Without `enable-linger`, the user service only starts when you open a graphical / SSH session. Logs : `journalctl --user -u gemini-bridge -f`.

## Manual cookie fallback

If you don't want to install the extension, paste cookies once in `server/config.conf` :

```ini
[Cookies]
gemini_cookie_1psid = g.a000…   # from DevTools → Application → Cookies → gemini.google.com
gemini_cookie_1psidts = sidts-… # same
```

The library auto-rotates `1PSIDTS` after that. Re-paste `1PSID` only when you log out.

## g4f fallback (ChatGPT, Claude, DeepSeek, Grok, …) — optional

The vendored `WebAI-to-API` ships a built-in `gpt4free` mode for many other LLMs. It's intended as an **emergency fallback when your daily Gemini quota is exhausted** (free / Pro / Ultra each have rolling limits — Google doesn't publish exact numbers, you only learn the cap by hitting it). Heavy install (~200 MB), not enabled by default.

**Install** :

```bash
WITH_G4F=1 ./start.sh                         # native (asks interactively if WITH_G4F unset)
WITH_G4F=1 docker compose build && docker compose up -d   # Docker
```

**Switch to g4f** (when Gemini is exhausted) — three options, all equivalent :

- Click **"Switch to g4f fallback"** in the extension popup (works in native, Docker, systemd — uses `POST /admin/mode`)
- Type `2` ⏎ in the `./start.sh` terminal (native foreground only)
- `curl -X POST -H 'Origin: chrome-extension://anything' -H 'Content-Type: application/json' -d '{"mode":"g4f"}' http://localhost:6969/admin/mode`

**Switch back to Gemini** : restart the bridge — `initial_mode` is hardcoded to `webai`, so any restart returns to Gemini :
- native : Ctrl+C in the `./start.sh` terminal then re-run (or type `1` ⏎)
- Docker : `docker compose restart`
- systemd : `systemctl --user restart gemini-bridge`

The asymmetry is intentional : the FastAPI worker (which exposes `/admin/mode`) is replaced by g4f's own server in g4f mode, so the HTTP switch is one-way. You typically restart on the next day after the Gemini quota window resets, so a manual restart is not a hot-path action.

### Auto-fallback on Gemini quota (transparent, but degraded)

When g4f is installed and Gemini fails (429 quota / 401 auth / 502 upstream / 504 timeout), the bridge automatically routes the same request through `g4f.client` in the same HTTP round-trip — OpenCode receives a 200 response instead of an error. The first failure also flips a **sticky window** (default 4h) so subsequent requests skip Gemini entirely and go direct to g4f, avoiding the 30s upstream timeout penalty per call.

#### What works

- **HTTP-level transparency** : OpenCode / curl / any OpenAI-compat client never sees the Gemini failure ; they just get a 200 with a different model's answer.
- **Sticky cache** : after the first successful fallback, the next ~4h of requests bypass Gemini directly (`reason=sticky` in logs). Override : `GEMINI_BRIDGE_FALLBACK_STICKY_HOURS=<n>` (`0` disables).
- **Manual reset** : *Retry Gemini now* button in the extension popup, or `POST /admin/reset-fallback`.
- **Visibility** :
  - Extension popup : `↪ Sticky fallback active · command-r-plus · 3.8h left` + reset button.
  - Response header `X-Bridge-Fallback: g4f:<model>:<reason>` (reason ∈ `quota`/`auth`/`upstream`/`timeout`/`sticky`).
  - Server logs : `[FALLBACK.TRY]` / `[FALLBACK.OK]` / `[FALLBACK.ERR]` lines.
  - Response `model` field is annotated `gemini-3-pro→g4f:<fallback-model>` (see *OpenCode UI caveat* below).

#### What's limited (be honest)

- **Tool calling is degraded or lost.** No-auth g4f providers (HuggingSpace, CohereForAI, Pollinations…) don't reliably preserve OpenAI's `tools[]` array. The fallback model often ignores tools or emits the legacy `[tool_call:name for args]` text format which the bridge maps back imperfectly (only `ls`/`cat`/`find`/`grep` → `bash`). Expect OpenCode agentic loops to either run with no tools (chat only) or hallucinate.
- **OpenCode UI keeps showing the Gemini model name.** OpenCode caches the requested model ID and ignores `response.model`. The annotated string `gemini-3-pro→g4f:command-r-plus` is set server-side but doesn't surface in the OpenCode UI. **Use the extension popup as the source of truth** for "am I currently in fallback ?".
- **Quality varies** by no-auth provider availability ; some days `command-r-plus` is fast, other days it's down and the bridge has to try `command-a` or `qwen-3-235b`.

#### Default model and overrides

Default : **`command-r-plus`** (Cohere Command R+, served by CohereForAI/HuggingSpace — no auth, decent at tool calling among free options).

Override via `GEMINI_BRIDGE_FALLBACK_MODEL=<id>`. Choices :

- **No-auth** (work today, may break tomorrow — daily updates at [Free-AI-Things/g4f-working](https://github.com/Free-AI-Things/g4f-working)) : `command-r-plus`, `command-a`, `qwen-3-235b`, `qwen-3-30b-a3b`. Quality is decent for chat, hit-or-miss for tools.
- **Auth-required** (need to configure g4f auth — see below) : `gpt-4.1`, `gpt-4o`, `claude-3.7-sonnet`, `grok-4`, etc. Full quality + reliable tool calling, but adds a setup step.

#### Want full-quality fallback ? Pass an API key

`g4f.client.Client` doesn't auto-read provider env vars (`OPENROUTER_API_KEY`, …). The bridge instead exposes its own pass-through : set `GEMINI_BRIDGE_FALLBACK_API_KEY` and we forward it to `g4f.client.Client(api_key=…)`. Pair with a model whose g4f provider expects that key.

Easiest path — OpenRouter (free tier) :

1. Get a key at [openrouter.ai/keys](https://openrouter.ai/keys).
2. `export GEMINI_BRIDGE_FALLBACK_API_KEY=sk-or-v1-…` (or set it in `docker-compose.yml` / your systemd unit).
3. `export GEMINI_BRIDGE_FALLBACK_MODEL=openai/gpt-4o` (or `anthropic/claude-3.7-sonnet`, `meta-llama/llama-4-maverick`, etc.).
4. Restart the bridge.

Now when Gemini fails, you get real GPT-4o / Claude with native tool calling — OpenCode loops keep running. Without this, the no-auth fallback (`command-r-plus`) is a "don't crash" safety net, not a Gemini replacement.

The manual `/admin/mode` switch (force-engage g4f for the whole session) is still there too, independent of the auto-fallback path.

### Monitoring Gemini quota / knowing when it kicks in

`gemini-webapi` doesn't expose a quota endpoint, so detection is **reactive** only :

- The bridge maps Google's upstream quota errors to HTTP **429** internally — that's what triggers the auto-fallback above. Without g4f, your client sees the 429.
- For a live look, open `https://gemini.google.com` in the browser — Google shows quota messages there ("You've reached your usage limit", banner color, etc.).
- No proactive "X requests left" counter exists.

g4f manages its own provider auth (no cookies needed for most providers). The Chrome extension only feeds the Gemini cookie path — it does not push anything to g4f.

## Tool-result truncation per tier

Each tool-result message is truncated (head + tail kept, middle replaced by an ellipsis marker) before being sent to Gemini. The cap depends on the model suffix :

| Model suffix | Tier | Cap per tool result |
|---|---|---|
| _(none)_ | Free | **~8 000 chars (~2k tokens)** |
| `-plus` | Pro | **~32 000 chars (~8k tokens)** |
| `-advanced` | Ultra | **~128 000 chars (~32k tokens)** |

The values are sized so a typical session (system prompt + multiple tool calls + responses) fits comfortably in the model's context window, with margin. Set `GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS=<n>` to force a single global cap regardless of model (legacy behavior).

If Gemini still aborts (typically when total prompt is too large for the tier), the bridge maps that to HTTP 502 — or HTTP 429 if the upstream message contains "Status: 429". The auto-fallback to g4f kicks in on 429/401 only ; silent aborts (502) bubble up to the client.

## Updating

```bash
git pull   # done — server/ is vendored, nothing else to do
```

To pull improvements from `Amm1rr/WebAI-to-API` upstream, run `dev/upstream-sync.sh` (re-clones a clean upstream, re-applies all patches in `patches/`, replaces `server/`). For maintainers only.

## Security model

- The Chrome extension requests `cookies` permission scoped to `https://*.google.com/*` only.
- The server rejects any `/auth/cookies` POST whose `Origin` header is not `chrome-extension://...`.
- The server binds to `localhost` only.
- No `--remote-debugging-port` is required.

Trust boundary : any other Chrome extension on the same browser can theoretically POST to `localhost:6969/auth/cookies` from a `chrome-extension://` origin. Audit your installed extensions.

## Known limitations

- **Synthetic SSE streaming** : `gemini-webapi` returns the full response in one shot ; the bridge then chunks it into SSE frames. Protocol-compliant but no typewriter effect.
- Multi-account selection works inside one Chrome profile. Multiple profiles = one bridge per profile (different ports).
- **No quota / usage tracking** : `gemini-webapi` doesn't expose remaining quota ; the `usage` block in responses is always zero. Check usage on `gemini.google.com`. The server maps upstream quota errors to HTTP 429.
- **Tool calling via shim**, not native. The bridge tells Gemini to emit `<<TOOL_CALL>>{json}<<END>>` blocks and parses those into OpenAI-shaped `tool_calls[]`. Tested end-to-end with OpenCode (Read / Edit / Bash / WebFetch). Parser tolerates missing `<<END>>` and legacy `[tool_call:…]` text via `ls/cat/find/grep` → `bash` mapping.

## Changing the server port

Default port is `6969`. To change :

1. **Server** : `GEMINI_BRIDGE_PORT=7000 ./start.sh` (env var read by `start.sh`).
2. **Extension** : edit the single line `SERVER_BASE_URL = "http://localhost:6969"` in `extension/providers.js`, then reload the extension in `chrome://extensions/`.

Both must match.

## Environment variables

| Name | Default | Effect |
|---|---|---|
| `GEMINI_BRIDGE_PORT` | `6969` | Server bind port (read by `start.sh`). |
| `GEMINI_BRIDGE_MAX_TOOL_RESULT_CHARS` | _tier-adaptive_ | Cap per tool-result message (head + tail kept). Without override : 8 KB free / 32 KB Pro (`-plus`) / 128 KB Ultra (`-advanced`). Setting this var forces a single global cap regardless of model. |
| `GEMINI_BRIDGE_FALLBACK_STICKY_HOURS` | `4` | After a successful auto-fallback to g4f, skip Gemini entirely for this many hours. `0` disables the sticky behavior (every request re-tries Gemini first). |
| `GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS` | `30` | Hard cutoff on a single Gemini call. `gemini-webapi` retries internally for up to ~120s ; we short-circuit here so the auto-fallback to g4f engages quickly. |
| `GEMINI_BRIDGE_DEBUG` | unset | When `1` / `true`, enables verbose logging (full message contents, prompt head/tail, full Gemini response body) to console + `/tmp/gemini-bridge-debug.log` (rotated at 10 MB). Default keeps only high-signal one-liners (`REQ.HEAD`, `SHIM`, `TRUNCATE`, `GEMINI.OK` summary, `GEMINI.ERR`). |
| `WITH_G4F` | unset | Set to `1` on first `./start.sh` (or in `WITH_G4F=1 docker compose build`) to install the optional g4f fallback non-interactively. |
| `GEMINI_BRIDGE_FALLBACK_MODEL` | `command-r-plus` | Model used when auto-fallback to g4f triggers on Gemini 429/401/502/504. Default is no-auth (Cohere Command R+ via HuggingSpace). Override with any `g4f.models` ID — see the README's *Auto-fallback* section for the no-auth shortlist. |
| `GEMINI_BRIDGE_FALLBACK_API_KEY` | unset | Forwarded to `g4f.client.Client(api_key=…)`. Required if your fallback model's g4f provider needs auth (e.g. OpenRouter, HuggingFace, Anthropic). Get keys at the provider's site (e.g. [openrouter.ai/keys](https://openrouter.ai/keys)) — g4f does not issue any key. |

## Running tests

```bash
cd server && .venv/bin/python -m unittest tests.test_shim -v
```

Covers the OpenAI tool-calling shim (`<<TOOL_CALL>>` extraction, missing `<<END>>` tolerance, legacy `[tool_call:…]` fallback, head+tail truncation).

## Repository layout

| Path | What it is |
|---|---|
| `extension/` | Chrome MV3 extension (cookie sync). |
| `server/` | Vendored [`Amm1rr/WebAI-to-API`](https://github.com/Amm1rr/WebAI-to-API) (MIT) with `patches/` applied. |
| `patches/` | Deltas vs upstream. |
| `examples/` | Drop-in client configs (e.g. `opencode.jsonc`). |
| `start.sh` | Native: first run installs, every run starts. |
| `Dockerfile` / `docker-compose.yml` | Loopback-bound container with named volume for config. |
| `dev/` | Maintainer tools (`upstream-sync.sh`). |
| `NOTICE.md` | Attribution. |

> `server/Dockerfile` is upstream legacy — ignore, the root `Dockerfile` is the supported one.

## Contributing

Patches in `patches/` are the source of truth vs `Amm1rr/WebAI-to-API@e9d22d8` ; `server/` is the result. If they drift, regenerate via `git diff > patches/N-name.patch` in a clean upstream checkout, then verify with `dev/upstream-sync.sh`. To add a new provider beyond Gemini, edit `extension/providers.js` (cookie names) and `server/src/app/endpoints/auth.py::update_cookies` (server-side refresh).

## License

MIT for the original code in this repo (extension, patches, scripts) — Copyright (c) 2026. The vendored `server/` keeps its own MIT license — see `server/LICENSE` and `NOTICE.md`.
