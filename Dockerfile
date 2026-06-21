FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências de sistema: PostgreSQL client, WeasyPrint (PDF export)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        python3-dev \
        # WeasyPrint deps
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        libcairo2 \
        libgirepository1.0-dev \
        gir1.2-pango-1.0 \
        # Cleanup
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python (camada cacheável)
COPY requirements.txt /app/
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Modelo de embedding do RAG baixado no build (evita cold-download em produção).
# Cache fora de /app/media (volume) e fora de /app (COPY) para não ser sobrescrito.
ENV HF_HOME=/opt/hf_cache
ENV SENTENCE_TRANSFORMERS_HOME=/opt/hf_cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

# Copia código
COPY . /app/

# Collectstatic no build (sem DB, sem GCS)
RUN SECRET_KEY=build-only \
    DEBUG=False \
    USE_GCS=False \
    DATABASE_URL="" \
    python manage.py collectstatic --noinput 2>/dev/null || true

# Permissões para scripts
RUN chmod +x start.sh release.sh start_worker_fast.sh start_worker_heavy.sh

CMD ["bash", "start.sh"]
