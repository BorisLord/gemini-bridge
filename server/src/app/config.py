import configparser
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Anchor to <repo>/server/config.conf so the file's location doesn't drift with
# the caller's CWD (tests run from the repo root used to spawn one there).
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.conf"


def load_config(config_file: str | Path = _DEFAULT_CONFIG_PATH) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    try:
        config.read(config_file, encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            f"Config file '{config_file}' not found. Creating a default one."
        )
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

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            config.write(f)
    except Exception as e:
        logger.error(f"Error writing to config file: {e}")

    return config


CONFIG = load_config()
