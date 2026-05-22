#!/bin/bash
set -e

echo "Running migrations..."
python3 manage.py migrate --noinput

echo "Collecting static files..."
python3 manage.py collectstatic --noinput 2>/dev/null || true

# Celery workers em background (compartilham volume /app/media)
echo "Starting Celery workers in background..."
celery -A config worker --loglevel=info --concurrency=${CELERY_FAST_CONCURRENCY:-4} --queues=fast --max-tasks-per-child=100 &
celery -A config worker --loglevel=info --concurrency=${CELERY_HEAVY_CONCURRENCY:-2} --queues=heavy --max-tasks-per-child=20 &

echo "Starting gunicorn ASGI (uvicorn worker)..."
exec gunicorn config.asgi:application \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:$PORT \
  --workers ${GUNICORN_WORKERS:-2} \
  --timeout 300 \
  --graceful-timeout 120 \
  --keep-alive 65 \
  --worker-tmp-dir /dev/shm \
  --log-file -
