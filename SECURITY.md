# Security

The bridge handles your **Google session cookies** (`__Secure-1PSID` / `-1PSIDTS`) — anyone with read access to them can impersonate you on Gemini. Treat them like a password.

**Defaults are safe**: server binds loopback only (`127.0.0.1:6969`), `config.conf` is `chmod 0600`, no telemetry. Outbound traffic only to `gemini.google.com` and (if enabled) `openrouter.ai`.

**Don't**: expose port 6969 to a network, commit `config.conf` or `.env` (already in `.gitignore`), or load the extension without reviewing `extension/` (~600 LoC, no build step, no obfuscation).

**Trust model on `/admin/*`**: the bridge requires `Origin: chrome-extension://…` or a non-empty `X-Extension-Id` header on admin endpoints. Both signals are spoofable by any local process — the actual defense is the loopback bind (`127.0.0.1`). Treat the header check as hygiene against accidental cross-extension calls, not as authentication. Don't run the bridge on a host where untrusted local users have shell access.

## Reporting a vulnerability

Open a private advisory: <https://github.com/BorisLord/gemini-bridge/security/advisories/new>. Don't file public issues for vulnerabilities.
