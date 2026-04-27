FROM python:3.13-slim

# uv for fast dependency install
RUN pip install --no-cache-dir uv

WORKDIR /app/server
COPY server/ ./

RUN uv venv --python 3.13 \
 && uv pip install --no-cache -q -r requirements.txt

RUN cp config.conf.example /opt/config.conf.default

ENV PATH="/app/server/.venv/bin:$PATH"

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
