"""
Integração com Perplexity AI — busca de jurisprudência na web.

Responsabilidade: pesquisa de jurisprudência e normas como fonte suplementar ao Vertex RAG.
Prioriza fontes .gov.br.

Portado de P-Jari_antigo/chat/integrations/perplexity.py.
"""

import hashlib
import logging
import os
import time

import requests

_log = logging.getLogger(__name__)

_RAG_CACHE_TTL = 86_400  # 24h


def _cache_key(query: str) -> str:
    digest = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]
    return f"rag:perplexity:{digest}"


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


class PerplexityClient:
    def __init__(self):
        self.api_key = os.environ.get("PERPLEXITY_API_KEY", "")
        self.url = "https://api.perplexity.ai/chat/completions"

    def search_tese(self, processo, tese: str) -> str:
        """
        Pesquisa jurisprudência para uma tese defensiva.
        Retorna texto com fundamentação encontrada.
        """
        if not self.api_key:
            return (
                "Simulação (Perplexity): Tese pesquisada. "
                "A tese é favorável segundo jurisprudência recente (REsp 123.456)."
            )

        # Cache
        key = _cache_key(tese)
        r = _get_redis()
        if r:
            try:
                cached = r.get(key)
                if cached:
                    return cached
            except Exception:
                pass

        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Você é um assessor jurídico especialista no JARI de Santa Catarina. "
                        "Sua pesquisa deve obrigatoriamente priorizar sites com domínios .gov.br "
                        "ou .sc.gov.br. Relacione apenas resoluções do CONTRAN, CETRAN-SC, MBFT "
                        "e CTB aplicáveis ao caso. Para toda lei citada, pesquise o LINK OFICIAL "
                        "DA WEB dela e devolva no formato Markdown clicável."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Pesquise jurisprudência oficial aplicável e a validade normativa "
                        f"da seguinte tese de defesa: {tese}"
                    ),
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            start_time = time.time()
            response = requests.post(self.url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            data = response.json()
            latency_ms = int((time.time() - start_time) * 1000)

            # Log de uso
            from pareceres.models import log_ia_request
            log_ia_request(
                processo,
                fase="Pesquisa Jurisprudência",
                provider="Perplexity",
                input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                output_tokens=data.get("usage", {}).get("completion_tokens", 0),
                latency_ms=latency_ms,
                model_name="sonar-pro",
            )

            resultado = data["choices"][0]["message"]["content"]

            # Cache
            if r:
                try:
                    r.setex(key, _RAG_CACHE_TTL, resultado)
                except Exception:
                    pass

            return resultado
        except Exception as e:
            _log.error("Erro Perplexity: %s", e)
            return f"Erro ao acessar Perplexity: {e}"
