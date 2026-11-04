#!/bin/bash
# Starts the API (with the embedded OCR worker) and the Next.js server in one
# container. If either process exits, the container exits so the platform restarts it.
set -euo pipefail

mkdir -p /data/files /data/work

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
