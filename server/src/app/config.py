import configparser
import contextlib
from pathlib import Path

from app.logger import logger

# Anchor to <repo>/server/config.conf so the file's location doesn't drift with
# the caller's CWD (tests run from the repo root used to spawn one there).
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.conf"


def load_config(config_file: str | Path = _DEFAULT_CONFIG_PATH) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    # configparser.read() silently ignores missing files (returns []) so no
    # FileNotFoundError to catch — write below creates the file unconditionally.
    try:
        config.read(config_file, encoding="utf-8")
    except Exception as e:
        logger.error(f"Error reading config file: {e}")

    if "Browser" not in config:
        config["Browser"] = {"name": "firefox"}
    if "Cookies" not in config:
        config["Cookies"] = {}
    if "Gemini" not in config:
        config["Gemini"] = {"default_model": "gemini-3-flash", "gem_id": ""}
    if "Proxy" not in config:
        config["Proxy"] = {"http_proxy": ""}

    path = Path(config_file)
    try:
        with path.open("w", encoding="utf-8") as f:
            config.write(f)
        # Cookies are session-equivalent secrets — owner-only from the start,
        # not just after the first refresh in `models.gemini._persist_cookies`.
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    except Exception as e:
        logger.error(f"Error writing to config file: {e}")

    return config


CONFIG = load_config()
