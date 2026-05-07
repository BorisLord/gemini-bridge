#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER="$ROOT/server"

if [[ ! -d "$SERVER" ]]; then
  echo "Error: $SERVER missing. Did 'git clone' fail?" >&2
  exit 1
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
fi

cd "$SERVER"

# Detect partial-install (venv exists but deps missing) and redo.
if [[ -d ".venv" ]]; then
  if ! import_err=$(.venv/bin/python -c "import uvicorn" 2>&1); then
    echo ">> Incomplete .venv detected — re-running setup"
    echo ">> import error was: $import_err"
    rm -rf .venv
  fi
fi

if [[ ! -d ".venv" ]]; then
  if ! command -v uv >/dev/null; then
    echo "Error: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
  fi
  echo ">> First-run setup"
  uv venv --python 3.13
  uv pip install -q -r requirements.txt

  if [[ ! -f config.ini ]]; then
    cp config.ini.example config.ini
  fi
  chmod 0600 config.ini
  echo
  echo ">> Setup done. Don't forget to load ./extension/ in chrome://extensions/"
  echo "   (toggle Developer mode → Load unpacked → select extension/)"
  echo
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
