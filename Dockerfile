FROM python:3.13-slim

ARG WITH_G4F=0

# uv for fast dependency install
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY server/ ./server/
WORKDIR /app/server

RUN uv venv --python 3.13 \
 && uv pip install --no-cache -q -r requirements.txt \
 && if [ "$WITH_G4F" = "1" ]; then uv pip install --no-cache -q "g4f>=6.8.2" python-multipart; fi

# Stash a default config; entrypoint copies it on first run only.
RUN cp config.conf.example /opt/config.conf.default

ENV PATH="/app/server/.venv/bin:$PATH"

EXPOSE 6969

COPY <<'EOF' /entrypoint.sh
#!/bin/sh
set -e
# Persist config.conf in /data (named volume), expose it at the path the app expects.
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
