# Notice

The `server/` directory contains a vendored copy of [Amm1rr/WebAI-to-API](https://github.com/Amm1rr/WebAI-to-API) (MIT License) pinned at commit `e9d22d8`, with the modifications listed in `patches/` applied on top.

Original copyright belongs to the upstream authors — see `server/LICENSE`. The patches and everything else in this repository are licensed under MIT, Copyright (c) 2026 (see `LICENSE`).

To re-vendor against a newer upstream commit, see `dev/upstream-sync.sh`.

## Modifications applied (patches/)

| Patch | What it does |
|---|---|
| `0001-pr70-deps-and-config.patch`     | Bumps `gemini-webapi` to ≥2.0.0 and relaxes transitive pins; updates Gemini 3 model list in config (from upstream PR #70 by hiyukoim). |
| `0002-pr67-streaming-merged.patch`    | Adds SSE streaming on `/v1/chat/completions`; updates Gemini 3 schema (merged from upstream PR #67 by gyy0592 + PR #70). |
| `0003-reinit-and-register-auth.patch` | Forces Gemini client reinit in worker process after multiprocessing fork (fixes "Future attached to a different loop"); registers the `/auth` router. |
| `0004-add-auth-cookies-endpoint.patch`| Adds `POST /auth/cookies/{provider}` for hot cookie rotation, `POST /auth/accounts/{provider}` for multi-account discovery, and `/u/{N}/` URL routing in the Gemini wrapper. |
