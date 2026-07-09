"""
Service Fase 5 — Geração do Parecer Técnico Final.

Responsabilidades:
1. Montar contexto completo (admissibilidade + teses + RAG)
2. Gerar parecer via Anthropic (Claude)
3. Salvar parecer e avançar para AUDITORIA
"""

import concurrent.futures
import logging
import re

from pareceres.estado import FaseProcesso
from pareceres.models import Parecer, log_audit
from . import ServiceResult

_log = logging.getLogger(__name__)


def _run_in_thread(fn, *args):
    """Executa `fn` numa thread worker e fecha as conexões de BD que ela abriu.

    As conexões do Django são thread-local: cada thread do executor abre as suas
    próprias e precisa fechá-las ao terminar, senão vazam. Fechar a partir da
    thread principal (com close_old_connections) seria errado — derrubaria a
    conexão da própria requisição/transação (quebra dentro de TestCase).
    """
    from django.db import connections
    try:
        return fn(*args)
    finally:
        connections.close_all()

# Regex para localizar a seção "PRESCRIÇÃO E DECADÊNCIA" no parecer
_RE_PRESCRICAO_SECTION = re.compile(
    r'(\*{0,2}PRESCRI[ÇC][ÃA]O E DECAD[ÊE]NCIA\*{0,2})'  # título (com ou sem negrito/acentos)
    r'([\s\S]*?)'                                            # conteúdo da seção
    r'(?=\*{0,2}(?:MATERIALIDADE|GARANTIAS|PARECER FINAL|VOTO)\*{0,2})',  # próxima seção
    re.IGNORECASE,
)


def _inject_deterministic_prescricao(parecer_text: str, texto_deterministico: str) -> str:
    """Substitui a seção PRESCRIÇÃO E DECADÊNCIA do LLM pelo texto determinístico."""
    match = _RE_PRESCRICAO_SECTION.search(parecer_text)
    if not match:
        _log.warning("[PARECER] Seção PRESCRIÇÃO E DECADÊNCIA não encontrada para injeção")
        return parecer_text

    titulo = match.group(1)
    replacement = f"{titulo}\n\n{texto_deterministico}\n\n"
    result = parecer_text[:match.start()] + replacement + parecer_text[match.end():]
    _log.info("[PARECER] Seção PRESCRIÇÃO E DECADÊNCIA substituída pelo texto determinístico")
    return result


def _trunc(texto: str, max_chars: int) -> str:
    if not texto or len(texto) <= max_chars:
        return texto or ""
    return texto[:max_chars] + f"\n... [truncado: {len(texto) - max_chars} chars omitidos]"


def execute(processo) -> ServiceResult:
    """
    Gera o parecer técnico final.

    Pré-condição: processo na fase PARECER ou PARECER_GERANDO.
    Pós-condição: Parecer criado, processo na fase AUDITORIA.
    """
    import time as _time
    from pareceres.integrations.anthropic import AnthropicClient
    from pareceres.integrations.vertex import VertexRAGClient
    from pareceres.integrations.perplexity import PerplexityClient
    from pareceres.prompts.phase_5 import build_system_instruction

    _log.info("[PARECER] START processo=%s", processo.id)
    t0 = _time.time()

    # Avançar para PARECER_GERANDO se ainda não está
    if processo.fase == FaseProcesso.PARECER:
        processo.avancar_fase(FaseProcesso.PARECER_GERANDO)

    # Buscar dados das fases anteriores
    try:
        adm = processo.admissibilidade
    except Exception:
        return ServiceResult.falha("Admissibilidade não encontrada.")

    teses = list(processo.teses.order_by("ordem"))

    # Determinar a tese e o resultado esperado
    _rota = adm.rota
    if _rota in ("A", "B", "C"):
        # Mérito prejudicado
        motivos = []
        if adm.flag_prescricao_punitiva:
            motivos.append("PRESCRIÇÃO PUNITIVA")
        if adm.flag_prescricao_intercorrente:
            motivos.append("PRESCRIÇÃO INTERCORRENTE TRIENAL")
        if adm.flag_prescricao_intercorrente_bienal:
            motivos.append("PRESCRIÇÃO INTERCORRENTE BIENAL")
        if adm.flag_decadencia:
            motivos.append("DECADÊNCIA")
        if adm.flag_tempestivo is False:
            motivos.append("INTEMPESTIVIDADE")
        tese_texto = f"MÉRITO PREJUDICADO ({' / '.join(motivos)})."
        vertex_result = "Não aplicável."
        perplexity_result = "Não aplicável por ausência de mérito."
    else:
        # Rota D — mérito com teses
        tese_texto = "\n".join(f"Tese {t.ordem}: {t.titulo}" for t in teses) if teses else "Sem teses."
        vertex_result = teses[0].vertex_resultado if teses else ""
        perplexity_result = teses[0].perplexity_resultado if teses else ""

        # Buscar RAG se ainda não temos
        if not vertex_result or not perplexity_result:
            vertex = VertexRAGClient()
            perplexity = PerplexityClient()
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
            try:
                v_future = executor.submit(_run_in_thread, vertex.search, processo, tese_texto) if not vertex_result else None
                p_future = executor.submit(_run_in_thread, perplexity.search_tese, processo, tese_texto) if not perplexity_result else None

                if v_future:
                    try:
                        vertex_result = v_future.result(timeout=90)
                    except Exception:
                        vertex_result = "Vertex AI indisponível."
                if p_future:
                    try:
                        perplexity_result = p_future.result(timeout=90)
                    except Exception:
                        perplexity_result = "Perplexity indisponível."
            finally:
                executor.shutdown(wait=False)

    # Buscar análise de teses (se existir)
    analise_tese_texto = ""
    if teses and teses[0].fundamentacao:
        analise_tese_texto = teses[0].fundamentacao

    # Nome do relator (usuário logado)
    relator_name = ""
    try:
        profile = processo.user.profile
        relator_name = profile.user.get_full_name() or profile.user.username
    except Exception:
        relator_name = processo.user.get_full_name() or processo.user.username

    system_instruction = build_system_instruction(relator_name)

    # ── Flags efetivas (julgador prevalece sobre automático) ────────────
    _flags_txt = (
        f"- Tempestividade: {'TEMPESTIVO' if adm.flag_tempestivo else 'INTEMPESTIVO'}\n"
        f"- Prescrição Punitiva: {'SIM' if adm.flag_prescricao_punitiva else 'NÃO'}\n"
        f"- Prescrição Intercorrente Trienal: {'SIM' if adm.flag_prescricao_intercorrente else 'NÃO'}\n"
        f"- Prescrição Intercorrente Bienal: {'SIM' if adm.flag_prescricao_intercorrente_bienal else 'NÃO'}\n"
        f"- Decadência: {'SIM' if adm.flag_decadencia else 'NÃO'}\n"
    )

    # ── Texto determinístico de Prescrição e Decadência (Fase 3) ────────
    _texto_prescricao = ""
    if adm.fundamentacoes and adm.fundamentacoes.get("texto_prescricao_decadencia"):
        _texto_prescricao = adm.fundamentacoes["texto_prescricao_decadencia"]

    # ── Montar prompt ─────────────────────────────────────────────────────
    prompt_user = (
        f"DADOS DO PROCESSO:\n"
        f"PA: {processo.pa}\n"
        f"SGPE: {processo.sgpe}\n"
        f"Recorrente: {processo.recorrente}\n"
        f"Data da Sessão: {processo.data_sessao.strftime('%d/%m/%Y') if processo.data_sessao else 'N/A'}\n"
        f"Relator: {relator_name}\n\n"
        f"FLAGS DO JULGADOR (resultado escolhido — PREVALECEM sobre qualquer cálculo automático):\n"
        f"{_flags_txt}\n"
        f"ROTA: {_rota}\n\n"
        f"ADMISSIBILIDADE (análise técnica de referência):\n{_trunc(adm.texto_resultado, 10_000)}\n\n"
        f"SEÇÃO PRESCRIÇÃO E DECADÊNCIA (TEXTO PRONTO — COPIE IPSIS LITERIS, NÃO ALTERE):\n"
        f"{_texto_prescricao}\n\n"
        f"TESE DA DEFESA:\n{_trunc(tese_texto, 3_000)}\n\n"
        f"ANÁLISE DE TESES:\n{_trunc(analise_tese_texto, 12_000)}\n\n"
        f"RESULTADO RAG NORMATIVO (Vertex):\n{_trunc(vertex_result, 6_000)}\n\n"
        f"PESQUISA JURISPRUDENCIAL (Perplexity):\n{_trunc(perplexity_result, 6_000)}\n\n"
        f"Gere o PARECER TÉCNICO FINAL conforme as regras do SYSTEM."
    )

    # ── Geração via Anthropic (Claude) ───────────────────────────────────
    anthropic = AnthropicClient()
    parecer_text = anthropic.generate_text(
        processo, prompt_user,
        system_prompt=system_instruction,
        max_tokens=8192,
        temperature=0.3,
        fase_label="Parecer F5",
    )

    if not parecer_text:
        _log.error("[PARECER] Anthropic falhou — processo=%s", processo.id)
        processo.fase = FaseProcesso.PARECER
        processo.save(update_fields=["fase"])
        return ServiceResult.falha("Erro ao gerar parecer: Anthropic indisponível.")

    parecer_text = parecer_text.strip()

    # ── Pós-processamento: forçar texto determinístico na seção Prescrição ──
    if _texto_prescricao:
        parecer_text = _inject_deterministic_prescricao(parecer_text, _texto_prescricao)

    if not parecer_text or len(parecer_text) < 200:
        processo.fase = FaseProcesso.PARECER
        processo.save(update_fields=["fase"])
        return ServiceResult.falha("Parecer gerado está vazio ou muito curto.")

    # ── Extrair Dossiê de Fontes (se presente) ────────────────────────────
    dossie = ""
    match_start = re.search(r'\*?\*?\*?DOSSIE_START\*?\*?\*?', parecer_text)
    match_end = re.search(r'\*?\*?\*?DOSSIE_END\*?\*?\*?', parecer_text)
    if match_start and match_end:
        dossie = parecer_text[match_start.end():match_end.start()].strip()
        parecer_text = parecer_text[:match_start.start()].rstrip().rstrip("-").rstrip()

    # ── Salvar parecer ────────────────────────────────────────────────────
    parecer_obj, _ = Parecer.objects.get_or_create(processo=processo)
    parecer_obj.texto_ia = parecer_text
    parecer_obj.provider = "Anthropic"
    parecer_obj.save()

    # Determinar resultado final — DETERMINÍSTICO pelas FLAGS do julgador
    # DEFERIDO se: prescrição punitiva, trienal, bienal ou decadência SIM,
    #              OU ao menos uma tese acolhida na Fase 4.
    # INDEFERIDO caso contrário.
    if any([
        adm.flag_prescricao_punitiva,
        adm.flag_prescricao_intercorrente,
        adm.flag_prescricao_intercorrente_bienal,
        adm.flag_decadencia,
    ]):
        processo.resultado_final = "DEFERIDO"
    elif adm.flag_tempestivo is False:
        # Intempestivo → NÃO CONHECIDO (mérito prejudicado)
        processo.resultado_final = "NAO_CONHECIDO"
    elif teses and any(t.acolhida for t in teses):
        processo.resultado_final = "DEFERIDO"
    else:
        processo.resultado_final = "INDEFERIDO"

    # Avançar fase
    processo.fase = FaseProcesso.AUDITORIA
    processo.save(update_fields=["fase", "resultado_final"])

    log_audit("fase", processo=processo, fase="parecer_gerado", dados={
        "provider": "Anthropic",
        "model": "claude-sonnet-4-6",
        "chars": len(parecer_text),
        "rota": _rota,
        "latency_s": round(_time.time() - t0, 1),
    })

    _log.info("[PARECER] OK processo=%s chars=%d rota=%s %.1fs",
              processo.id, len(parecer_text), _rota, _time.time() - t0)
    return ServiceResult.sucesso(
        "Parecer gerado.",
        texto=parecer_text,
        dossie=dossie,
        rota=_rota,
    )
