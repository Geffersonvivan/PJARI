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
import os
import re
import tempfile

from django.core.files.storage import default_storage

from pareceres.estado import FaseProcesso
from pareceres.models import AnaliseTese, CacheTese, log_audit
from . import ServiceResult

_log = logging.getLogger(__name__)


def _obter_md_defesa(doc_consolidado, paginas_str: str) -> str | None:
    """
    Obtém o texto MD das páginas de defesa a partir do OCR salvo na Fase 2.
    Retorna texto MD filtrado pelas páginas de defesa, ou None se não disponível.
    """
    extracao = doc_consolidado.extracao_json or {}
    ocr_md = extracao.get("ocr_markdown")
    if not ocr_md:
        return None

    # Se "todas", retornar MD completo
    if not paginas_str or paginas_str.strip().lower() == "todas":
        return ocr_md

    # Filtrar apenas as páginas de defesa do MD
    try:
        paginas = set()
        for parte in paginas_str.split(","):
            parte = parte.strip()
            if "-" in parte:
                ini, fim = parte.split("-", 1)
                for p in range(int(ini.strip()), int(fim.strip()) + 1):
                    paginas.add(p)
            else:
                paginas.add(int(parte))

        # Parsear MD por seções "## Página N"
        blocos = re.split(r'\n*---\n*', ocr_md)
        filtrados = []
        for bloco in blocos:
            m = re.match(r'## Página (\d+)', bloco)
            if m and int(m.group(1)) in paginas:
                filtrados.append(bloco.strip())

        if filtrados:
            return "\n\n---\n\n".join(filtrados)

        # Fallback: retornar MD completo se parsing falhar
        return ocr_md
    except Exception:
        return ocr_md


def _extrair_paginas_defesa(file_path: str, paginas_str: str) -> str | None:
    """
    Recorta apenas as páginas de defesa do PDF e salva num arquivo temporário.
    Retorna o path do arquivo temporário, ou None se falhar.

    paginas_str: formato "54-56", "3,5,7-9", "todas" ou vazio.
    """
    if not paginas_str or paginas_str.strip().lower() == "todas":
        return None  # Usar PDF completo

    try:
        import fitz  # PyMuPDF

        # Parsear ranges: "54-56" → [53,54,55], "3,5" → [2,4]
        paginas = set()
        for parte in paginas_str.split(","):
            parte = parte.strip()
            if "-" in parte:
                ini, fim = parte.split("-", 1)
                for p in range(int(ini.strip()), int(fim.strip()) + 1):
                    paginas.add(p - 1)  # 0-indexed
            else:
                paginas.add(int(parte) - 1)

        with default_storage.open(file_path, "rb") as f:
            pdf_bytes = f.read()

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total = len(doc)
        paginas_validas = sorted(p for p in paginas if 0 <= p < total)

        if not paginas_validas:
            doc.close()
            return None

        novo = fitz.open()
        for p in paginas_validas:
            novo.insert_pdf(doc, from_page=p, to_page=p)
        doc.close()

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(novo.tobytes())
        tmp.close()
        novo.close()

        _log.info("[TESES-EXTRACAO] PDF recortado: páginas %s (%d págs) de %d total",
                  paginas_str, len(paginas_validas), total)
        return tmp.name

    except Exception as e:
        _log.warning("[TESES-EXTRACAO] Erro ao recortar páginas '%s': %s — usando PDF completo",
                     paginas_str, e)
        return None

# Regex para separar blocos de análise por tese (ex: "**Tese 1 –", "**Tese 2 –")
_RE_TESE_BLOCK = re.compile(
    r'(?=\*{0,2}Tese\s+(\d+)\s*[–\-—])',
    re.IGNORECASE,
)


def _distribuir_fundamentacao(teses, analise_texto):
    """
    Parseia o texto de análise do Gemini e distribui cada bloco
    à AnaliseTese correspondente pelo número da tese.
    Se não conseguir parsear, salva tudo na primeira tese como fallback.
    """
    blocos = _RE_TESE_BLOCK.split(analise_texto)

    # _RE_TESE_BLOCK.split produz: [preâmbulo, num1, bloco1, num2, bloco2, ...]
    tese_map = {}
    i = 1
    while i < len(blocos) - 1:
        try:
            num = int(blocos[i])
        except (ValueError, TypeError):
            i += 2
            continue
        texto_bloco = blocos[i + 1].strip()
        # Acumular (pode haver múltiplos blocos para a mesma tese)
        if num in tese_map:
            tese_map[num] += "\n\n" + texto_bloco
        else:
            tese_map[num] = f"**Tese {num} –" + texto_bloco
        i += 2

    if tese_map:
        for tese in teses:
            bloco = tese_map.get(tese.ordem, "")
            if bloco:
                tese.fundamentacao = bloco
                tese.save(update_fields=["fundamentacao"])
        _log.info("[TESES-ANALISE] Fundamentação distribuída: %s blocos para %s teses",
                  len(tese_map), len(teses))
    else:
        # Fallback: salvar tudo na primeira tese
        if teses:
            teses[0].fundamentacao = analise_texto
            teses[0].save(update_fields=["fundamentacao"])
        _log.warning("[TESES-ANALISE] Não foi possível parsear blocos — fallback primeira tese")


# Placeholders de "sem teses" — títulos canônicos. A view (api_teses_dados)
# reconhece esses títulos exatos para exibir o estado "sem teses" em vez de
# renderizá-los como uma tese defensiva real (#1).
TITULO_SEM_TESES = "Nenhuma tese defensiva identificada na peça recursal."
TITULO_EXTRACAO_INDISPONIVEL = (
    "Extração automática de teses indisponível no momento. "
    "Verifique manualmente as teses da defesa."
)
PLACEHOLDER_TITULOS = frozenset({TITULO_SEM_TESES, TITULO_EXTRACAO_INDISPONIVEL})


def is_placeholder_tese(titulo) -> bool:
    """True se o título é um placeholder de 'sem teses' (não uma tese real)."""
    return (titulo or "").strip() in PLACEHOLDER_TITULOS


def _marcar_sem_teses(processo, titulo, motivo_audit):
    """Cria uma tese-placeholder, avança para TESE_AGUARDANDO e retorna sucesso.

    Convergência única para os casos em que não há teses a analisar: o LLM não
    identificou nenhuma OU a extração ficou indisponível. Em ambos o julgador
    chega à tela de teses e decide manualmente — em vez de a task ficar em retry
    e o front girar até o timeout de 5 min.
    """
    processo.teses.all().delete()
    AnaliseTese.objects.create(processo=processo, ordem=1, titulo=titulo)
    processo.avancar_fase(FaseProcesso.TESE_AGUARDANDO)
    log_audit("fase", processo=processo, fase="teses_sem_extracao",
              dados={"motivo": motivo_audit})
    _log.info("[TESES-EXTRACAO] OK (sem teses: %s) processo=%s", motivo_audit, processo.id)
    return ServiceResult.sucesso(
        "Nenhuma tese identificada. O julgador pode verificar manualmente.",
        teses_count=0,
    )


def _extrair_teses_gemini(processo, doc, paginas, texto_md, prompt_texto, system_instruction):
    """Extrai teses via Gemini (texto MD ou upload do PDF). Retorna str (pode ser ""). Lança em falha."""
    import time as _time
    from pareceres.integrations.gemini import GeminiClient

    gemini = GeminiClient()
    if texto_md:
        _log.info("[TESES-EXTRACAO] Gemini via MD (%d chars) processo=%s", len(texto_md), processo.id)
        contents = [prompt_texto]
    else:
        _log.info("[TESES-EXTRACAO] Gemini via PDF processo=%s", processo.id)
        pdf_recortado = _extrair_paginas_defesa(doc.arquivo.name, paginas)
        if pdf_recortado:
            uploaded = gemini.upload_file_from_path(pdf_recortado)
            try:
                os.unlink(pdf_recortado)
            except Exception:
                pass
        else:
            uploaded = gemini.upload_file(doc.arquivo.name)
        if not uploaded:
            raise RuntimeError("Falha no upload do PDF para o Gemini")
        contents = [uploaded, (
            "O documento anexo contém EXCLUSIVAMENTE a defesa recursal do recorrente. "
            "Extraia TODAS as teses defensivas apresentadas, cada uma separadamente, "
            "no formato exato: 'Tese N: [resumo]'."
        )]

    t0 = _time.time()
    response, model_used = gemini.generate(
        model="gemini-2.5-flash",
        contents=contents,
        config={
            "temperature": 0.1,
            "max_output_tokens": 2048,
            "system_instruction": system_instruction,
        },
        timeout_per_call=90,
    )
    gemini.log_usage(processo, response, "Extração Tese F4", model_used, t0)
    return (response.text or "").strip()


def _extrair_teses_llm(processo, doc, paginas, texto_md, prompt_texto, system_instruction):
    """Gemini → (fallback) Anthropic.

    Retorna:
      str  — teses extraídas (pode ser "" quando o LLM não identifica teses)
      None — ambos os provedores indisponíveis (o chamador degrada graciosamente)
    """
    # 1. Gemini (primário)
    try:
        return _extrair_teses_gemini(processo, doc, paginas, texto_md, prompt_texto, system_instruction)
    except Exception as e:
        _log.error("[TESES-EXTRACAO] Gemini falhou: %s — processo=%s%s", e, processo.id,
                   " — tentando Anthropic" if prompt_texto else "")

    # 2. Anthropic (fallback — só pela via textual do OCR; sem MD não há como cair aqui)
    if prompt_texto:
        try:
            from pareceres.integrations.anthropic import AnthropicClient
            texto = AnthropicClient().generate_text(
                processo, prompt_texto, system_prompt=system_instruction,
                max_tokens=2048, temperature=0.1, fase_label="Extração Tese F4 (fallback)",
            )
            # generate_text retorna None em erro (não levanta). Trate None como
            # falha do provedor → retorne None (degradação), em vez de "" (que
            # seria lido como "LLM não achou teses" e mostraria a mensagem errada). (#3)
            if texto is None:
                _log.error("[TESES-EXTRACAO] Anthropic retornou None (erro) — processo=%s", processo.id)
            else:
                _log.info("[TESES-EXTRACAO] Fallback Anthropic OK processo=%s", processo.id)
                return texto.strip()
        except Exception as e:
            _log.error("[TESES-EXTRACAO] Anthropic também falhou: %s — processo=%s", e, processo.id)

    return None


def execute_extracao(processo) -> ServiceResult:
    """
    Extrai a tese defensiva do PDF consolidado via Gemini, com fallback Anthropic.

    Pré-condição: processo na fase TESE.
    Pós-condição: AnaliseTese criada com o título da tese.
    Se a extração ficar indisponível, degrada para "sem teses" (não trava o front).
    """
    from pareceres.prompts.phase_4 import SYSTEM_INSTRUCTION_EXTRACT

    _log.info("[TESES-EXTRACAO] START processo=%s", processo.id)

    # Buscar documento consolidado (contém a defesa recursal)
    doc_consolidado = processo.documentos.filter(tipo="consolidado").first()
    if not doc_consolidado:
        doc_consolidado = processo.documentos.filter(tipo="autuacao").first()

    if not doc_consolidado:
        return ServiceResult.falha("Nenhum documento encontrado para extração de teses.")

    # Texto OCR (MD) da Fase 2 — habilita a via textual (Gemini e fallback Anthropic)
    paginas_defesa = processo.paginas_defesa or "todas"
    texto_md = _obter_md_defesa(doc_consolidado, paginas_defesa)

    prompt_texto = None
    if texto_md:
        prompt_texto = (
            "# DEFESA RECURSAL (OCR Markdown)\n\n"
            f"{texto_md}\n\n"
            "---\n\n"
            "Extraia TODAS as teses defensivas apresentadas pelo recorrente acima, "
            "cada uma separadamente, no formato exato: 'Tese N: [resumo]'."
        )

    tese_extraida = _extrair_teses_llm(
        processo, doc_consolidado, paginas_defesa, texto_md, prompt_texto,
        SYSTEM_INSTRUCTION_EXTRACT,
    )

    # Ambos os provedores caíram → degrada graciosamente (não deixa a task em
    # retry nem o front girando): avança com placeholder para tratamento manual.
    if tese_extraida is None:
        _log.error("[TESES-EXTRACAO] Extração indisponível (Gemini+Anthropic) — processo=%s", processo.id)
        return _marcar_sem_teses(
            processo,
            TITULO_EXTRACAO_INDISPONIVEL,
            motivo_audit="Gemini e Anthropic indisponíveis",
        )

    # LLM respondeu, mas sem teses → placeholder e segue (julgador decide)
    if not tese_extraida:
        return _marcar_sem_teses(
            processo,
            TITULO_SEM_TESES,
            motivo_audit="LLM não identificou teses no PDF",
        )

    # Criar registros de tese
    # Parsear múltiplas teses — aceita variações de formato:
    # "Tese 1: ...", "Tese 1 – ...", "1. ...", "1) ...", "- Tese 1: ..."
    teses_raw = re.split(
        r'(?:^|\n)\s*(?:'
        r'(?:[-•]\s*)?(?:Tese|TESE)\s+\d+\s*[:\-–—.]'  # Tese 1: / Tese 1 –
        r'|\d+\s*[.)]\s*'                                 # 1. / 1)
        r')',
        tese_extraida,
    )
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
            model="gemini-2.5-flash",
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

    # Parsear análise por tese e distribuir fundamentação individualmente
    # O Gemini gera blocos "**Tese X – Síntese..." para cada tese
    _distribuir_fundamentacao(teses, analise_texto)

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
