#!/bin/bash
set -e

echo "Starting Celery worker - fila FAST (fases 1-4)..."
exec celery -A config worker \
    --loglevel=info \
    --concurrency=${CELERY_FAST_CONCURRENCY:-16} \
    --queues=fast \
    --max-tasks-per-child=100
