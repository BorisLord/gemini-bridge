# Security

The bridge handles your **Google session cookies** (`__Secure-1PSID` / `-1PSIDTS`) — anyone with read access to them can impersonate you on Gemini. Treat them like a password.

**Defaults are safe**: server binds loopback only (`127.0.0.1:6969`), `config.conf` is `chmod 0600`, no telemetry. Outbound traffic only to `gemini.google.com` and (if enabled) `openrouter.ai`.

**Don't**: expose port 6969 to a network, commit `config.conf` or `.env` (already in `.gitignore`), or load the extension without reviewing `extension/` (~600 LoC, no build step, no obfuscation).

## Reporting a vulnerability

Open a private advisory: <https://github.com/BorisLord/gemini-bridge/security/advisories/new>. Don't file public issues for vulnerabilities.
