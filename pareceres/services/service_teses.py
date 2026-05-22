"""
Service Fase 4 — Extração e análise de teses defensivas.

Responsabilidades:
1. Extração da tese principal do PDF via Gemini
2. Consulta paralela a Vertex RAG + Perplexity
3. Análise cruzada das teses via Gemini
4. Avançar para TESE_AGUARDANDO
"""

import concurrent.futures
import logging
import re

from pareceres.estado import FaseProcesso
from pareceres.models import AnaliseTese, CacheTese, log_audit
from . import ServiceResult

_log = logging.getLogger(__name__)


def execute_extracao(processo) -> ServiceResult:
    """
    Extrai a tese defensiva do PDF consolidado via Gemini.

    Pré-condição: processo na fase TESE.
    Pós-condição: AnaliseTese criada com o título da tese.
    Se nenhuma tese identificada, retorna com flag skip_to_parecer=True.
    """
    from pareceres.integrations.gemini import GeminiClient
    from pareceres.prompts.phase_4 import SYSTEM_INSTRUCTION_EXTRACT

    _log.info("[TESES-EXTRACAO] START processo=%s", processo.id)

    gemini = GeminiClient()

    # Buscar documento consolidado (contém a defesa recursal)
    doc_consolidado = processo.documentos.filter(tipo="consolidado").first()
    if not doc_consolidado:
        doc_consolidado = processo.documentos.filter(tipo="autuacao").first()

    if not doc_consolidado:
        return ServiceResult.falha("Nenhum documento encontrado para extração de teses.")

    # Upload + extração
    uploaded = gemini.upload_file(doc_consolidado.arquivo.name)
    if not uploaded:
        return ServiceResult.falha("Não foi possível processar o documento para extração de teses.")

    paginas_defesa = processo.paginas_defesa or "todas"
    prompt_user = (
        f"Analise a defesa recursal nas páginas {paginas_defesa} do documento. "
        "Extraia TODAS as teses defensivas apresentadas pelo recorrente."
    )

    try:
        import time as _time
        t0 = _time.time()
        response, model_used = gemini.generate(
            model="gemini-2.0-flash",
            contents=[uploaded, prompt_user],
            config={
                "temperature": 0.1,
                "max_output_tokens": 2048,
                "system_instruction": SYSTEM_INSTRUCTION_EXTRACT,
            },
            timeout_per_call=90,
        )
        gemini.log_usage(processo, response, "Extração Tese F4", model_used, t0)
        tese_extraida = (response.text or "").strip()
    except Exception as e:
        _log.error("[TESES-EXTRACAO] Erro Gemini: %s — processo=%s", e, processo.id)
        return ServiceResult.falha(f"Erro ao extrair teses: {e}")

    # Tese vazia → INDEFERIDO por ausência de fundamentação (§425-427)
    if not tese_extraida:
        _log.warning("[TESES-EXTRACAO] Nenhuma tese identificada — processo=%s", processo.id)
        AnaliseTese.objects.create(
            processo=processo,
            ordem=1,
            titulo="Nenhuma tese defensiva identificada na peça recursal.",
        )
        return ServiceResult.sucesso(
            "Nenhuma tese identificada.",
            skip_to_parecer=True,
            motivo="Ausência de fundamentação recursal (§425-427).",
        )

    # Criar registros de tese
    # Parsear múltiplas teses do texto (formato "Tese 1: ...", "Tese 2: ..." etc.)
    teses_raw = re.split(r'(?:^|\n)\s*(?:Tese|TESE)\s+\d+\s*[:\-–—]', tese_extraida)
    teses_raw = [t.strip() for t in teses_raw if t.strip()]

    if not teses_raw:
        teses_raw = [tese_extraida]

    # Limpar teses anteriores deste processo
    processo.teses.all().delete()

    for i, titulo in enumerate(teses_raw, 1):
        AnaliseTese.objects.create(
            processo=processo,
            ordem=i,
            titulo=titulo[:500],
        )

    _log.info("[TESES-EXTRACAO] OK processo=%s teses=%d", processo.id, len(teses_raw))
    return ServiceResult.sucesso(
        f"{len(teses_raw)} tese(s) extraída(s).",
        teses_count=len(teses_raw),
    )


def execute_analise(processo) -> ServiceResult:
    """
    Analisa as teses via Vertex RAG + Perplexity + Gemini.

    Pré-condição: AnaliseTese(s) existem para o processo.
    Pós-condição: teses com fundamentação preenchida, processo em TESE_AGUARDANDO.
    """
    from pareceres.integrations.gemini import GeminiClient
    from pareceres.integrations.vertex import VertexRAGClient
    from pareceres.integrations.perplexity import PerplexityClient
    from pareceres.prompts.phase_4 import SYSTEM_INSTRUCTION_ANALYZE

    _log.info("[TESES-ANALISE] START processo=%s", processo.id)

    teses = list(processo.teses.order_by("ordem"))
    if not teses:
        return ServiceResult.falha("Nenhuma tese encontrada para análise.")

    gemini = GeminiClient()
    vertex = VertexRAGClient()
    perplexity = PerplexityClient()

    # Consolidar teses em texto único para busca
    tese_texto = "\n".join(f"Tese {t.ordem}: {t.titulo}" for t in teses)

    # ── Cache check ───────────────────────────────────────────────────────
    vertex_result = None
    perplexity_result = None

    # Buscar cache por núcleo da tese
    import hashlib
    nucleo = hashlib.sha256(tese_texto[:500].encode()).hexdigest()[:16]
    chave = f"tese_{nucleo}"
    cache_entry = CacheTese.objects.filter(cache_key=chave).first()
    if cache_entry:
        vertex_result = cache_entry.vertex_resultado
        perplexity_result = cache_entry.perplexity_resultado
        cache_entry.hit_count += 1
        cache_entry.save(update_fields=["hit_count"])
        _log.info("[TESES-ANALISE] Cache hit: %s — processo=%s", chave, processo.id)

    # ── Busca externa (cache miss) ────────────────────────────────────────
    if not vertex_result or not perplexity_result:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            v_future = executor.submit(vertex.search, processo, tese_texto)
            p_future = executor.submit(perplexity.search_tese, processo, tese_texto)

            try:
                vertex_result = v_future.result(timeout=120)
            except Exception as e:
                _log.warning("[TESES-ANALISE] Vertex erro/timeout: %s — processo=%s", e, processo.id)
                vertex_result = ""

            try:
                perplexity_result = p_future.result(timeout=150)
            except Exception as e:
                _log.warning("[TESES-ANALISE] Perplexity erro/timeout: %s — processo=%s", e, processo.id)
                perplexity_result = ""
        finally:
            executor.shutdown(wait=False)
            from django.db import close_old_connections
            close_old_connections()

        # Salvar no cache
        if vertex_result or perplexity_result:
            try:
                CacheTese.objects.create(
                    cache_key=chave,
                    vertex_resultado=vertex_result or "",
                    perplexity_resultado=perplexity_result or "",
                )
            except Exception:
                pass

    # Salvar resultados RAG nas teses
    for tese in teses:
        tese.vertex_resultado = vertex_result or ""
        tese.perplexity_resultado = perplexity_result or ""
        tese.save(update_fields=["vertex_resultado", "perplexity_resultado"])

    # ── Análise via Gemini ────────────────────────────────────────────────
    try:
        adm = processo.admissibilidade
    except Exception:
        adm = None

    adm_texto = adm.texto_resultado if adm else ""

    prompt_user = (
        f"TESES DEFENSIVAS:\n{tese_texto}\n\n"
        f"RESULTADO RAG (Inventário Normativo):\n{vertex_result or 'Não disponível'}\n\n"
        f"RESULTADO PERPLEXITY (Jurisprudência web):\n{perplexity_result or 'Não disponível'}\n\n"
        f"ADMISSIBILIDADE:\n{adm_texto[:4000]}\n\n"
        "Analise cada tese conforme as regras do SYSTEM."
    )

    try:
        import time as _time
        t0 = _time.time()
        response, model_used = gemini.generate(
            model="gemini-2.0-flash",
            contents=[{"role": "user", "parts": [{"text": prompt_user}]}],
            config={
                "temperature": 0.2,
                "max_output_tokens": 6144,
                "system_instruction": SYSTEM_INSTRUCTION_ANALYZE,
            },
            timeout_per_call=120,
        )
        gemini.log_usage(processo, response, "Análise Tese F4", model_used, t0)
        analise_texto = (response.text or "").strip()
    except Exception as e:
        _log.error("[TESES-ANALISE] Erro Gemini: %s — processo=%s", e, processo.id)
        return ServiceResult.falha(f"Erro ao analisar teses: {e}")

    # Salvar análise na primeira tese (texto completo da análise cruzada)
    if teses:
        teses[0].fundamentacao = analise_texto
        teses[0].save(update_fields=["fundamentacao"])

    # Avançar fase
    processo.avancar_fase(FaseProcesso.TESE_AGUARDANDO)

    log_audit("fase", processo=processo, fase="teses_analisadas", dados={
        "teses_count": len(teses),
        "tem_vertex": bool(vertex_result),
        "tem_perplexity": bool(perplexity_result),
    })

    _log.info("[TESES-ANALISE] OK processo=%s", processo.id)
    return ServiceResult.sucesso(
        "Teses analisadas.",
        analise=analise_texto,
        teses_count=len(teses),
    )
