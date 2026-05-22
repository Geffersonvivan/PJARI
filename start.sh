#!/bin/bash
set -e

echo "Running migrations..."
python3 manage.py migrate --noinput

echo "Collecting static files..."
python3 manage.py collectstatic --noinput 2>/dev/null || true

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
