"""
Service Fase 2 — Extração de dados do PDF consolidado via LLM.

Extrai nome do recorrente, tipo de penalidade, datas do processo.

Provider: Gemini (upload PDF nativo) com fallback Anthropic (PDF→JPEG→Claude)
"""

import datetime
import logging
import re
import time

from pareceres.estado import FaseProcesso
from pareceres.models import Admissibilidade, log_audit
from . import ServiceResult

_log = logging.getLogger(__name__)

# ── Prompt de extração ──────────────────────────────────────────────────────

PROMPT_EXTRACAO_F2 = (
    "Analise o PDF de um processo de infração de trânsito e extraia APENAS os campos abaixo.\n"
    "Para datas use SEMPRE o formato DD/MM/AAAA. Se não encontrar um campo, escreva NAO_ENCONTRADO.\n\n"
    "RECORRENTE: (nome completo do recorrente/autuado em MAIÚSCULAS)\n"
    "TIPO_PENALIDADE: (multa/advertencia/suspensao/cassacao/nao_determinado)\n"
    "INFRACAO_DOCUMENTO: (descrição curta da infração no AIT)\n"
    "DATA_INFRACAO: (data da infração/cometimento no AIT, formato DD/MM/AAAA)\n"
    "DATA_NA: (data da Notificação de Autuação, formato DD/MM/AAAA)\n"
    "DATA_NP: (data da Notificação de Penalidade, formato DD/MM/AAAA)\n"
    "DATA_INSTAURACAO: (data de instauração do processo administrativo, formato DD/MM/AAAA)\n\n"
    "Responda SOMENTE com as 7 linhas acima, sem nenhum outro texto."
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _extrair_campo(texto_resposta: str, label: str) -> str:
    m = re.search(rf'{label}:\s*(.+)', texto_resposta, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_date_br(s: str):
    """Tenta parsear data BR DD/MM/AAAA. Retorna date ou None."""
    if not s:
        return None
    s = s.strip().replace("-", "/")
    try:
        return datetime.datetime.strptime(s, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def _salvar_campos(processo, adm, texto_resposta: str):
    """Parse e salva nome do recorrente, tipo de penalidade e datas extraídas."""
    recorrente = _extrair_campo(texto_resposta, "RECORRENTE")
    tipo_penalidade = _extrair_campo(texto_resposta, "TIPO_PENALIDADE").lower()
    infracao_doc = _extrair_campo(texto_resposta, "INFRACAO_DOCUMENTO")
    data_infracao = _extrair_campo(texto_resposta, "DATA_INFRACAO")
    data_na = _extrair_campo(texto_resposta, "DATA_NA")
    data_np = _extrair_campo(texto_resposta, "DATA_NP")
    data_instauracao = _extrair_campo(texto_resposta, "DATA_INSTAURACAO")

    _log.info("[DOCUMENTOS] Campos extraidos processo=%s: recorrente=%r tipo=%r infracao=%r "
              "data_infracao=%r data_na=%r data_np=%r data_inst=%r",
              processo.id, recorrente, tipo_penalidade, infracao_doc,
              data_infracao, data_na, data_np, data_instauracao)

    _nulos = {"nulo", "null", "none", "n/a", "não encontrado", "nao encontrado",
              "não localizado", "nao localizado", "nao_se_aplica", "nao_encontrado", ""}

    # Salvar no Processo
    if recorrente and recorrente.lower() not in _nulos:
        processo.recorrente = recorrente[:255].upper()
        processo.save(update_fields=["recorrente"])

    # Salvar na Admissibilidade
    if tipo_penalidade in ("multa", "advertencia", "suspensao", "cassacao"):
        adm.tipo_penalidade = tipo_penalidade

    if infracao_doc and infracao_doc.lower() not in _nulos:
        adm.infracao_documento = infracao_doc[:255]

    # Datas extraídas pela LLM
    parsed = _parse_date_br(data_infracao)
    if parsed:
        adm.data_infracao = parsed

    parsed = _parse_date_br(data_na)
    if parsed:
        adm.data_na = parsed

    parsed = _parse_date_br(data_np)
    if parsed:
        adm.data_np = parsed

    parsed = _parse_date_br(data_instauracao)
    if parsed:
        adm.data_instauracao = parsed

    adm.save()


# ── Providers ────────────────────────────────────────────────────────────────


def _tentar_gemini(processo, docs_dict: dict) -> str | None:
    """Tenta extração via Gemini (upload PDF nativo)."""
    try:
        from pareceres.integrations.gemini import GeminiClient
        gemini = GeminiClient()
        if not gemini.client:
            _log.warning("[DOCUMENTOS] Gemini sem API key configurada")
            return None

        consolidado_path = docs_dict.get("consolidado")
        if not consolidado_path:
            return None

        _log.info("[DOCUMENTOS] Gemini upload PDF processo=%s", processo.id)
        start = time.time()
        uploaded = gemini.upload_file(consolidado_path)
        if not uploaded:
            _log.error("[DOCUMENTOS] Gemini upload falhou processo=%s", processo.id)
            return None

        _log.info("[DOCUMENTOS] Gemini generate processo=%s", processo.id)
        response, model_used = gemini.generate(
            model="gemini-2.0-flash",
            contents=[uploaded, PROMPT_EXTRACAO_F2],
            config={"temperature": 0.1, "max_output_tokens": 1024},
        )

        texto = response.text if response else None
        gemini.log_usage(processo, response, "Extração F2", model_used, start)

        if texto:
            _log.info("[DOCUMENTOS] Gemini OK processo=%s modelo=%s", processo.id, model_used)
        return texto
    except Exception as e:
        _log.error("[DOCUMENTOS] Gemini falhou: %s", e, exc_info=True)
        return None


def _tentar_anthropic(processo, docs_dict: dict) -> str | None:
    """Tenta extração via Anthropic (PDF→JPEG→Claude) — fallback."""
    try:
        from pareceres.integrations.anthropic import AnthropicClient
        client = AnthropicClient()
        return client.extract_documentos(processo, docs_dict, PROMPT_EXTRACAO_F2)
    except Exception as e:
        _log.error("[DOCUMENTOS] Anthropic falhou: %s", e)
        return None


# ── Execução principal ───────────────────────────────────────────────────────


def execute(processo) -> ServiceResult:
    """
    Extrai nome do recorrente do PDF consolidado.

    Pré-condição: processo na fase DOCUMENTOS ou DOCUMENTOS_EXTRAINDO.
    Pós-condição: processo na fase DOCUMENTOS_EXTRAIDOS, Admissibilidade criada/atualizada.
    """
    _log.info("[DOCUMENTOS] START processo=%s", processo.id)

    adm, _ = Admissibilidade.objects.get_or_create(processo=processo)

    docs = {d.tipo: d for d in processo.documentos.all()}
    if "consolidado" not in docs:
        return ServiceResult.falha("Documento consolidado não encontrado.")

    docs_dict = {tipo: d.arquivo.name for tipo, d in docs.items() if d.arquivo}

    # Extração via Gemini (PDF nativo) → fallback Anthropic
    texto_resposta = _tentar_gemini(processo, docs_dict)
    provider = "Gemini"

    if not texto_resposta:
        _log.info("[DOCUMENTOS] Gemini indisponível, tentando Anthropic processo=%s", processo.id)
        texto_resposta = _tentar_anthropic(processo, docs_dict)
        provider = "Anthropic"

    if not texto_resposta:
        return ServiceResult.falha(
            "Não foi possível processar nenhum documento. "
            "Verifique se os arquivos são PDFs válidos."
        )

    _log.info("[DOCUMENTOS] Resposta LLM processo=%s provider=%s:\n%s",
              processo.id, provider, texto_resposta[:1000])

    # Parse e salvar campos
    _salvar_campos(processo, adm, texto_resposta)

    # Avançar fase
    processo.avancar_fase(FaseProcesso.DOCUMENTOS_EXTRAIDOS)

    log_audit("fase", processo=processo, fase="documentos_extraidos",
              dados={"provider": provider, "docs_processados": list(docs_dict.keys())})

    _log.info("[DOCUMENTOS] OK processo=%s provider=%s docs=%s",
              processo.id, provider, list(docs_dict.keys()))
    return ServiceResult.sucesso("Documentos extraídos com sucesso.")
