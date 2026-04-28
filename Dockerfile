# syntax=docker/dockerfile:1.7

# Stage 1 — builder: build the venv with uv (binary copied from upstream).
FROM python:3.13-slim AS builder

# uv as a single static binary; pin for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy

WORKDIR /app/server

# Deps layer caches independently of source — re-runs only when requirements.txt changes.
COPY server/requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python 3.13 .venv \
 && uv pip install --python .venv/bin/python -r requirements.txt \
 && find .venv -depth \
      \( -type d \( -name '__pycache__' -o -name 'tests' -o -name 'test' \) \
      -o -type f \( -name '*.pyc' -o -name '*.pyi' \) \) \
      -exec rm -rf '{}' +

# Stage 2 — runtime: copy venv only; uv stays out of the final image.
FROM python:3.13-slim
WORKDIR /app/server
COPY --from=builder /app/server/.venv /app/server/.venv
COPY server/src ./src
COPY server/pyproject.toml server/config.conf.example server/LICENSE ./
RUN cp config.conf.example /opt/config.conf.default

ENV PATH="/app/server/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 6969

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys,os; \
    sys.exit(0 if urllib.request.urlopen(f'http://localhost:{os.environ.get(\"GEMINI_BRIDGE_PORT\",\"6969\")}/healthz', timeout=2).status == 200 else 1)"

COPY <<'EOF' /entrypoint.sh
#!/bin/sh
set -e
# Persist config.conf in /data (named volume); app expects it under /app/server.
mkdir -p /data
if [ ! -f /data/config.conf ]; then
  cp /opt/config.conf.default /data/config.conf
fi
chmod 0600 /data/config.conf
ln -sf /data/config.conf /app/server/config.conf
cd /app/server
exec python src/run.py --host 0.0.0.0 --port "${GEMINI_BRIDGE_PORT:-6969}" "$@"
EOF
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
