import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from loguru import logger as _loguru

# 5 MB × 4 backups = ~20 MB cap at <repo>/server/logs/bridge.log.
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "bridge.log"

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 4
_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

_console = logging.StreamHandler(sys.stderr)
_console.setFormatter(logging.Formatter(_FMT))
_file = RotatingFileHandler(
    LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
)
_file.setFormatter(logging.Formatter(_FMT))

logging.basicConfig(level=logging.INFO, handlers=[_console, _file])
logger = logging.getLogger("app")


# gemini-webapi logs through loguru; mirror those records into stdlib logging
# so they land in the rotated file alongside ours. Loguru passes a
# `Message` (str subclass) whose `.record` is the structured dict.
def _to_stdlib(message):
    record = message.record
    level = logging.getLevelName(record["level"].name)
    if not isinstance(level, int):
        # Loguru's SUCCESS/TRACE have no stdlib equivalent.
        level = logging.INFO
    logging.getLogger(record["name"]).log(level, record["message"])


_loguru.remove()
_loguru.add(_to_stdlib, level="INFO")
