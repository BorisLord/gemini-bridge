#!/usr/bin/env bash
# Maintenance script — NOT for end users.
# Re-vendors ./server from a fresh upstream checkout + reapplies all patches.
# Run when bumping UPSTREAM_REF to pull in upstream improvements.

set -euo pipefail

UPSTREAM_REPO="https://github.com/Amm1rr/WebAI-to-API.git"
UPSTREAM_REF="${UPSTREAM_REF:-e9d22d82615a16f3fc54efadbd82e62e201bd8df}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="$ROOT/server"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo ">> Cloning $UPSTREAM_REPO @ $UPSTREAM_REF into $TMP"
git clone --quiet "$UPSTREAM_REPO" "$TMP/upstream"
cd "$TMP/upstream"
git reset --hard --quiet "$UPSTREAM_REF"
git clean -fdq

echo ">> Applying patches"
for p in "$ROOT"/patches/*.patch; do
  echo "   - $(basename "$p")"
  git apply --whitespace=nowarn "$p"
done

# Preserve user-local files when overwriting server/
PRESERVE=("config.conf" ".venv" ".gemini_webapi")
TMPSAVE="$(mktemp -d)"
for f in "${PRESERVE[@]}"; do
  [[ -e "$SERVER/$f" ]] && mv "$SERVER/$f" "$TMPSAVE/"
done

echo ">> Replacing $SERVER"
rm -rf "$SERVER"
mv "$TMP/upstream" "$SERVER"
rm -rf "$SERVER/.git"

for f in "${PRESERVE[@]}"; do
  [[ -e "$TMPSAVE/$f" ]] && mv "$TMPSAVE/$f" "$SERVER/"
done

echo
echo "Done. Now: review changes (git diff), test (./start.sh --setup-only && ./start.sh)."
