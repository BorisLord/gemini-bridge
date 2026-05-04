"""Single source of truth for env-var reads. Module constants for boot-time
switches; functions for values that mutate between requests (tests stub them,
or they compose with `config.conf` in `gemini_client._resolve_*`)."""
import os

_TRUTHY = ("1", "true", "yes", "on")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return raw.lower() in _TRUTHY


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _optional(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


ENABLE_DOCS: bool = _bool("GEMINI_BRIDGE_ENABLE_DOCS")
DEBUG: bool = _bool("GEMINI_BRIDGE_DEBUG")
# DEBUG implies DUMP_PROMPTS — verbose mode is useless without the actual prompts.
DUMP_PROMPTS: bool = DEBUG or _bool("GEMINI_BRIDGE_DUMP_PROMPTS")

# Gemini Web silently aborts above ~100 KB on Pro models — override only if you've measured otherwise.
MAX_PROMPT_CHARS: int = _int("GEMINI_BRIDGE_MAX_PROMPT_CHARS", 100_000)

PROMPT_DUMP_RETAIN: int = 30  # last.txt is always kept on top of those.
# Passed to gemini-webapi `init(timeout=...)` — caps streaming + polling lib-side.
# Bump for Ultra users running deep_research / large file analysis.
REQUEST_TIMEOUT_SECONDS: float = _float("GEMINI_BRIDGE_REQUEST_TIMEOUT_SECONDS", 90.0)
# Per-tier char budgets per tool result (free 32k tok, Pro/Ultra 1M tok).
TIER_TOOL_RESULT_CAPS: dict[str, int] = {"free": 8_000, "plus": 32_000, "advanced": 128_000}

DEBUG_LOG_PATH: str = "/tmp/gemini-bridge-debug.log"
DEBUG_LOG_MAX_BYTES: int = 10 * 1024 * 1024

# Pinned generic desktop Chrome — Google flags `python-httpx` as a bot otherwise.
PROBE_USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def cookie_1psid_env() -> str | None:
    return _optional("GEMINI_COOKIE_1PSID")


def cookie_1psidts_env() -> str | None:
    return _optional("GEMINI_COOKIE_1PSIDTS")


def account_index_env() -> int | None:
    """`None` = not set / invalid → callers fall back to config.conf."""
    raw = os.environ.get("GEMINI_BRIDGE_ACCOUNT_INDEX")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def initial_gem_id_env() -> str | None:
    return _optional("GEMINI_BRIDGE_GEM_ID")


def selected_account_id_env() -> str | None:
    """Cross-browser selector (`<browser>:<account_index>`, e.g. `firefox:1`)
    consumed by `_resolve_selected_account_id` at boot. Higher precedence than
    the same key in `[Cookies]` of `config.conf` so a Docker/CI run can pin
    its session purely from the environment."""
    return _optional("GEMINI_BRIDGE_SELECTED_ACCOUNT_ID")
