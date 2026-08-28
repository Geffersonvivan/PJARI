#!/bin/bash
set -e

# GCP credentials from env var (Document AI, GCS)
if [ -n "$GCP_CREDENTIALS_JSON" ]; then
  echo "$GCP_CREDENTIALS_JSON" > /app/gcp-credentials.json
  export GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json
  echo "GCP credentials written to /app/gcp-credentials.json"
fi

echo "Running migrations..."
python3 manage.py migrate --noinput

echo "Collecting static files..."
python3 manage.py collectstatic --noinput 2>/dev/null || true

# Migração one-time do volume /app/media → GCS (guardada por flag).
# Ative MIGRATE_MEDIA=1 num deploy, confira o log "Concluído", depois remova a
# flag. Idempotente: pula o que já existe no bucket.
if [ "$MIGRATE_MEDIA" = "1" ]; then
  echo "MIGRATE_MEDIA=1 → copiando mídia local para o GCS..."
  python3 manage.py migrate_media_to_gcs || echo "AVISO: migrate_media_to_gcs falhou (ver logs acima)"
fi

# Celery workers em background (compartilham volume /app/media)
echo "Starting Celery workers in background..."
celery -A config worker --loglevel=info --concurrency=${CELERY_FAST_CONCURRENCY:-4} --queues=fast --max-tasks-per-child=100 2>&1 &
celery -A config worker --loglevel=info --concurrency=${CELERY_HEAVY_CONCURRENCY:-2} --queues=heavy --max-tasks-per-child=20 2>&1 &

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
