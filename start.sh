#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER="$ROOT/server"

if [[ ! -d "$SERVER" ]]; then
  echo "Error: $SERVER missing. Did 'git clone' fail?" >&2
  exit 1
fi

cd "$SERVER"

# First-run setup. Detect partial-install (venv exists but deps missing) and redo.
if [[ -d ".venv" ]] && ! .venv/bin/python -c "import uvicorn" 2>/dev/null; then
  echo ">> Incomplete .venv detected — re-running setup"
  rm -rf .venv
fi

if [[ ! -d ".venv" ]]; then
  if ! command -v uv >/dev/null; then
    echo "Error: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
  fi
  echo ">> First-run setup"
  uv venv --python 3.13
  uv pip install -q -r requirements.txt

  if [[ -z "${WITH_G4F:-}" ]] && [[ -t 0 ]]; then
    read -r -p "Install g4f fallback (~200MB; ChatGPT/Claude/DeepSeek/Grok)? [y/N] " ans
    case "$ans" in y|Y|yes|YES) WITH_G4F=1 ;; esac
  fi
  if [[ "${WITH_G4F:-0}" == "1" ]]; then
    echo ">> Installing g4f"
    # python-multipart is a transitive dep of g4f's API server — its absence
    # crashes the g4f mode at startup, so we always pull it alongside.
    uv pip install -q "g4f>=6.8.2" python-multipart
  fi

  if [[ ! -f config.conf ]]; then
    cp config.conf.example config.conf
  fi
  chmod 0600 config.conf
  echo
  echo ">> Setup done. Don't forget to load ./extension/ in chrome://extensions/"
  echo "   (toggle Developer mode → Load unpacked → select extension/)"
  echo
fi

# Allow `WITH_G4F=1 ./start.sh` to add g4f on top of an existing .venv.
# python-multipart is a g4f runtime dep — install both so g4f mode boots cleanly.
if [[ "${WITH_G4F:-0}" == "1" ]] && ! .venv/bin/python -c "import g4f" 2>/dev/null; then
  echo ">> Installing g4f into existing venv"
  uv pip install -q "g4f>=6.8.2" python-multipart
fi
# Repair: a previous WITH_G4F=1 install (or pre-fix venv) may have skipped
# python-multipart. Pull it in now so g4f mode never crashes on missing dep.
if .venv/bin/python -c "import g4f" 2>/dev/null && ! .venv/bin/python -c "import multipart" 2>/dev/null; then
  echo ">> Repairing g4f deps (python-multipart)"
  uv pip install -q python-multipart
fi

if [[ "${1:-}" == "--setup-only" ]]; then
  echo "Setup complete. Run ./start.sh to launch the server."
  exit 0
fi

# shellcheck source=/dev/null
source .venv/bin/activate

PORT_ARGS=()
if [[ -n "${GEMINI_BRIDGE_PORT:-}" ]]; then
  PORT_ARGS=(--port "$GEMINI_BRIDGE_PORT")
fi

exec python src/run.py "${PORT_ARGS[@]}" "$@"
