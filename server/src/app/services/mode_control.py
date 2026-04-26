"""Cross-process mode-switch flag.

The supervisor in run.py polls a small file every second; any process with
filesystem access (the FastAPI worker handling /admin/mode) can drop a request
in it. This is what makes the webai/g4f switch reachable from Docker and
systemd, where stdin (the original `1`/`2` keyboard switch) is unavailable.
"""

import os
from pathlib import Path
from typing import Optional

MODE_FILE = Path(
    os.environ.get("GEMINI_BRIDGE_MODE_FILE", "/tmp/gemini-bridge-mode-request")
)

VALID_MODES = ("webai", "g4f")


def request_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode}")
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_FILE.write_text(mode)


def consume_pending_mode() -> Optional[str]:
    try:
        mode = MODE_FILE.read_text().strip()
    except FileNotFoundError:
        return None
    try:
        MODE_FILE.unlink()
    except FileNotFoundError:
        pass
    return mode if mode in VALID_MODES else None
