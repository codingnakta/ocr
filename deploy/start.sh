#!/bin/bash
# Starts the API (with the embedded OCR worker) and the Next.js server in one
# container. If either process exits, the container exits so the platform restarts it.
set -euo pipefail

mkdir -p /data/files /data/work

if [ "${AUTH_MODE:-}" = "password" ] && [ -z "${ADMIN_PASSWORD:-}" ] && [ -z "${ALLOW_NO_ADMIN:-}" ]; then
  echo "ERROR: ADMIN_PASSWORD is not set. Add it as a secret (Render: Environment, Hugging Face: Settings > Variables and secrets)." >&2
  exit 1
fi
# A session secret is required in production. If none is configured, generate one
# for this run (users simply have to log in again after a restart).
if [ -z "${SESSION_SECRET:-}" ]; then
  SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  export SESSION_SECRET
  echo "SESSION_SECRET not set: generated a temporary one (sessions end on restart)." >&2
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "WARNING: ANTHROPIC_API_KEY is not set: only the free local OCR mode will work." >&2
fi

cd /app/backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 &
api=$!

# wait until the API answers (model loading can take a few seconds)
for _ in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$api" 2>/dev/null; then
    echo "API failed to start" >&2
    exit 1
  fi
  sleep 1
done

cd /app/web
HOSTNAME=0.0.0.0 PORT="${PORT:-10000}" node server.js &
web=$!

trap 'kill -TERM $api $web 2>/dev/null; wait' TERM INT
wait -n "$api" "$web"
status=$?
echo "a process exited (status $status); stopping container" >&2
kill -TERM "$api" "$web" 2>/dev/null || true
exit "$status"
