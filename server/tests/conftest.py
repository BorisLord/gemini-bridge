"""Shared pytest/unittest setup. Loaded automatically before any test module
is imported, so guards posted here are guaranteed to win the race against
module-level side effects."""

import os

# Block fallback._persist() from rewriting the real config.conf when tests
# poke set_enabled / set_api_key / set_model on the in-memory state.
os.environ.setdefault("GEMINI_BRIDGE_DISABLE_PERSIST", "1")
