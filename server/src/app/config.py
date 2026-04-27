import configparser
import logging

logger = logging.getLogger(__name__)


def load_config(config_file: str = "config.conf") -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    try:
        # Explicit UTF-8: configparser defaults to locale encoding on Windows.
        config.read(config_file, encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            f"Config file '{config_file}' not found. Creating a default one."
        )
    except Exception as e:
        logger.error(f"Error reading config file: {e}")

    if "Browser" not in config:
        config["Browser"] = {"name": "chrome"}
    if "Cookies" not in config:
        config["Cookies"] = {}
    if "Gemini" not in config:
        config["Gemini"] = {"default_model": "gemini-3-flash", "gem_id": ""}
    if "Proxy" not in config:
        config["Proxy"] = {"http_proxy": ""}
    if "OpenRouter" not in config:
        config["OpenRouter"] = {"enabled": "true", "api_key": "", "model": ""}

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            config.write(f)
    except Exception as e:
        logger.error(f"Error writing to config file: {e}")

    return config


CONFIG = load_config()
