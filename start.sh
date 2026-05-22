#!/bin/bash
set -e

echo "Starting gunicorn ASGI (uvicorn worker)..."
exec gunicorn config.asgi:application \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:$PORT \
  --workers ${GUNICORN_WORKERS:-4} \
  --timeout 300 \
  --graceful-timeout 120 \
  --keep-alive 65 \
  --worker-tmp-dir /dev/shm \
  --log-file -
