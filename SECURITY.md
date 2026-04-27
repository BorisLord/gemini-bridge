# Security

## Threat model

`gemini-bridge` proxies your **personal Google Gemini session** through a local server. The cookies it handles (`__Secure-1PSID`, `__Secure-1PSIDTS`) are equivalent to a logged-in Google session — anyone with read access to them can impersonate you on Gemini.

## What the bridge does

- Binds **loopback only** (`127.0.0.1:6969`). It is not reachable from your LAN by default.
- Stores cookies in `server/config.conf` (chmod-restricted to your user) or in environment variables you provide.
- Sends no telemetry. The only outbound traffic is to `gemini.google.com` and (if enabled) `openrouter.ai`.
- The Chrome extension only reads Google cookies and pushes them to `127.0.0.1:6969`. Source is auditable in `extension/`.

## What you should do

- Do **not** expose port 6969 to the network or the internet. Use SSH tunnelling if you need remote access.
- Do **not** commit `server/config.conf` or `.env` — they are in `.gitignore`.
- Treat `__Secure-1PSID` like a password. Rotate by signing out of Google if you suspect leakage.
- Review the `extension/` source before loading it unpacked. It is small and auditable (~600 LoC, no build step, no obfuscation).

## Reporting a vulnerability

Open a private security advisory on GitHub:
<https://github.com/BorisLord/gemini-bridge/security/advisories/new>

Please do not file public issues for vulnerabilities.
