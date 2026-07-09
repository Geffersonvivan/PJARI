"""
Integração com Vertex AI — RAG contra Inventário Normativo.

Responsabilidade: buscar fundamentação legal no datastore (Discovery Engine).
É o "GPS" do sistema — nunca deve ser bypassado para referências legais.

Portado de P-Jari_antigo/chat/integrations/vertex.py.
"""

import hashlib
import logging
import math
import os
import re
import threading

_log = logging.getLogger(__name__)

_RAG_CACHE_TTL = 86_400  # 24h

# Singleton do modelo de embedding
_embed_model = None
_embed_lock = threading.Lock()
_MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")


def _rag_cache_key(prefix: str, query: str) -> str:
    """Chave Redis determinística para resultado de query RAG."""
    normalized = re.sub(r"[.\s]+", " ", query.strip().lower())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"rag:{prefix}:{digest}"


def _get_redis():
    try:
        from django.conf import settings
        import redis
        return redis.from_url(
            getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
    except Exception:
        return None


def _get_embed_model():
    """Retorna modelo SentenceTransformer singleton (lazy load)."""
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    with _embed_lock:
        if _embed_model is not None:
            return _embed_model
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer(_MODEL_NAME)
            _log.info("Modelo de embedding carregado: %s", _MODEL_NAME)
        except ImportError:
            raise RuntimeError(
                "sentence-transformers não instalado. "
                "pip install sentence-transformers"
            )
        return _embed_model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Gera embeddings usando sentence-transformers."""
    model = _get_embed_model()
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VertexRAGClient:
    """Cliente para busca RAG no Inventário Normativo via Vertex AI."""

    def __init__(self):
        from django.conf import settings
        self.project_id = settings.VERTEX_PROJECT_ID
        self.location = settings.VERTEX_LOCATION
        self.datastore_id = settings.VERTEX_RAG_DATASTORE_ID

    def search(self, processo, query: str, top_k: int = 5) -> str:
        """
        Busca no RAG do Inventário Normativo.
        Retorna texto formatado com os resultados ou fallback em caso de erro.
        """
        from django.conf import settings

        # Backend local (pgvector) é o padrão — Vertex é legado opcional.
        if getattr(settings, "RAG_BACKEND", "local") == "local":
            return self._search_local(query, top_k, processo=processo)

        if not self.project_id or not self.datastore_id:
            _log.warning("Vertex RAG não configurado (PROJECT_ID ou DATASTORE_ID vazios)")
            return self._search_local(query, top_k, processo=processo)

        # Cache Redis
        cache_key = _rag_cache_key("vertex", query)
        r = _get_redis()
        if r:
            try:
                cached = r.get(cache_key)
                if cached:
                    return cached
            except Exception:
                pass

        try:
            import time
            from google.cloud import discoveryengine_v1beta as de

            start_time = time.time()
            client = de.SearchServiceClient()
            serving_config = (
                f"projects/{self.project_id}/locations/{self.location}/"
                f"collections/default_collection/dataStores/{self.datastore_id}/"
                f"servingConfigs/default_serving_config"
            )

            request = de.SearchRequest(
                serving_config=serving_config,
                query=query,
                page_size=top_k,
            )
            response = client.search(request=request)

            results = []
            for result in response.results:
                doc = result.document
                title = doc.derived_struct_data.get("title", "")
                snippet = ""
                for s in doc.derived_struct_data.get("snippets", []):
                    snippet += s.get("snippet", "")
                results.append(f"**{title}**\n{snippet}")

            texto = "\n\n---\n\n".join(results) if results else "Nenhum resultado encontrado no RAG."

            latency_ms = int((time.time() - start_time) * 1000)

            # Log de uso
            from pareceres.models import log_ia_request
            log_ia_request(
                processo,
                fase="RAG Vertex",
                provider="Vertex",
                latency_ms=latency_ms,
                dados={"query": query[:200], "results_count": len(results)},
            )

            # Cache
            if r and results:
                try:
                    r.setex(cache_key, _RAG_CACHE_TTL, texto)
                except Exception:
                    pass

            return texto
        except Exception as e:
            _log.error("Erro Vertex RAG: %s — fallback para busca local", e)
            return self._search_local(query, top_k, processo=processo)

    def _search_local(self, query: str, top_k: int = 5, processo=None) -> str:
        """
        Busca vetorial local no Inventário Normativo via pgvector.

        O ranking por similaridade de cosseno roda dentro do Postgres
        (operador `<=>` + índice HNSW). Retorna os top_k trechos formatados.
        """
        import time

        from pgvector.django import CosineDistance

        from pareceres.models import DocumentoNormativo

        # Cache Redis
        cache_key = _rag_cache_key("local", query)
        r = _get_redis()
        if r:
            try:
                cached = r.get(cache_key)
                if cached:
                    return cached
            except Exception:
                pass

        try:
            start_time = time.time()
            q_emb = embed_texts([query])[0]

            rows = list(
                DocumentoNormativo.objects
                .exclude(embedding__isnull=True)
                .annotate(dist=CosineDistance("embedding", q_emb))
                .order_by("dist")
                .values("nome_arquivo", "pagina_inicio", "texto", "dist")[:top_k]
            )

            if not rows:
                return "Nenhum normativo indexado na base local."

            partes = []
            for d in rows:
                similaridade = 1.0 - float(d["dist"])
                partes.append(
                    f"**{d['nome_arquivo']} (p.{d['pagina_inicio']}, "
                    f"relevância {similaridade:.0%})**\n{d['texto']}"
                )
            texto = "\n\n---\n\n".join(partes)

            latency_ms = int((time.time() - start_time) * 1000)
            try:
                from pareceres.models import log_ia_request
                log_ia_request(
                    processo,
                    fase="RAG Local",
                    provider="RAG-pgvector",
                    latency_ms=latency_ms,
                    dados={"query": query[:200], "results_count": len(rows)},
                )
            except Exception:
                pass

            if r:
                try:
                    r.setex(cache_key, _RAG_CACHE_TTL, texto)
                except Exception:
                    pass

            return texto
        except Exception as e:
            _log.error("Erro busca local pgvector: %s", e, exc_info=True)
            return "Erro na busca de fundamentação normativa."
