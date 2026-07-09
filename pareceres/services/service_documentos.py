"""
Service Fase 2 — Extração de dados do PDF consolidado via LLM.

Extrai nome do recorrente, tipo de penalidade, datas do processo.

Pipeline de extração (4 camadas de assertividade):
1. Prompt melhorado com few-shot + confiança por campo
2. Consenso multi-modelo (Gemini + Claude) — divergências = confiança rebaixada
3. Validação cruzada de datas (regras temporais)
4. Confidence score salvo por campo → UI mostra verde/amarelo/vermelho
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
    "Você é um especialista em documentos de trânsito brasileiros (AIT, NA, NP, recurso JARI).\n"
    "Analise o PDF de um processo de infração de trânsito e extraia os campos abaixo.\n\n"
    "REGRAS:\n"
    "- Datas SEMPRE no formato DD/MM/AAAA.\n"
    "- Se não encontrar um campo com certeza, escreva NAO_ENCONTRADO.\n"
    "- Para cada campo, informe o nível de CONFIANÇA: ALTA, MEDIA ou BAIXA.\n"
    "  - ALTA: campo encontrado com texto claro e inequívoco no documento.\n"
    "  - MEDIA: campo inferido pelo contexto ou com leitura parcialmente legível.\n"
    "  - BAIXA: campo ambíguo, múltiplas datas candidatas, ou texto borrado.\n\n"
    "DICAS PARA IDENTIFICAR CADA DATA:\n"
    "- DATA_INFRACAO: aparece no AIT (Auto de Infração) como 'Data da Infração', "
    "'Data/Hora da Infração', 'Cometimento'. É a data mais antiga.\n"
    "- DATA_NA: aparece na Notificação de Autuação como 'Data da Notificação', "
    "'Expedição da NA', 'Notificado em'. Vem DEPOIS da infração.\n"
    "- DATA_NP: aparece na Notificação de Penalidade como 'Data da Notificação de Penalidade', "
    "'Imposição de Penalidade', 'Expedição da NP'. Vem DEPOIS da NA.\n"
    "- DATA_INSTAURACAO: aparece no recurso/requerimento como 'Data de Instauração', "
    "'Protocolo do Recurso', 'Data de Abertura'. Geralmente é próxima ou igual à DATA_NP.\n"
    "- DATA_SESSAO: aparece na Ata de Julgamento, Pauta da Sessão, ou convocação como "
    "'Data da Sessão', 'Sessão de Julgamento', 'Pauta dia'. É uma data futura ou recente.\n"
    "- DATA_PROTOCOLO: aparece no comprovante de protocolo do recurso como "
    "'Data de Protocolo', 'Protocolado em', 'Recebido em'. É quando o recurso foi registrado.\n"
    "- PRAZO_PROTOCOLO: aparece na Notificação de Penalidade como "
    "'Prazo para recurso até', 'Data limite', 'Prazo final'. "
    "É a data limite para protocolar o recurso JARI.\n\n"
    "EXEMPLO DE RESPOSTA:\n"
    "RECORRENTE: JOSE DA SILVA SANTOS | CONFIANCA: ALTA\n"
    "TIPO_PENALIDADE: multa | CONFIANCA: ALTA\n"
    "INFRACAO_DOCUMENTO: Avançar o sinal vermelho do semáforo (art. 208 CTB) | CONFIANCA: ALTA\n"
    "DATA_INFRACAO: 15/03/2022 | CONFIANCA: ALTA\n"
    "DATA_NA: 20/04/2022 | CONFIANCA: MEDIA\n"
    "DATA_NP: NAO_ENCONTRADO | CONFIANCA: BAIXA\n"
    "DATA_INSTAURACAO: 10/06/2022 | CONFIANCA: ALTA\n"
    "DATA_SESSAO: 15/08/2024 | CONFIANCA: MEDIA\n"
    "DATA_PROTOCOLO: 10/06/2022 | CONFIANCA: ALTA\n"
    "PRAZO_PROTOCOLO: 25/06/2022 | CONFIANCA: MEDIA\n\n"
    "CAMPOS PARA EXTRAIR (responda SOMENTE com as 10 linhas, sem nenhum outro texto):\n"
    "RECORRENTE: (nome completo do recorrente/autuado em MAIÚSCULAS) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "TIPO_PENALIDADE: (multa/advertencia/suspensao/cassacao/nao_determinado) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "INFRACAO_DOCUMENTO: (descrição curta da infração no AIT) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "DATA_INFRACAO: (data da infração/cometimento no AIT, DD/MM/AAAA) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "DATA_NA: (data da Notificação de Autuação, DD/MM/AAAA) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "DATA_NP: (data da Notificação de Penalidade, DD/MM/AAAA) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "DATA_INSTAURACAO: (data de instauração do processo, DD/MM/AAAA) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "DATA_SESSAO: (data da sessão de julgamento JARI, DD/MM/AAAA) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "DATA_PROTOCOLO: (data de protocolo do recurso JARI, DD/MM/AAAA) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
    "PRAZO_PROTOCOLO: (prazo/data limite para protocolar o recurso, DD/MM/AAAA) | CONFIANCA: (ALTA/MEDIA/BAIXA)\n"
)

_NULOS = {"nulo", "null", "none", "n/a", "não encontrado", "nao encontrado",
           "não localizado", "nao localizado", "nao_se_aplica", "nao_encontrado", ""}

_CAMPOS_DATA = ("DATA_INFRACAO", "DATA_NA", "DATA_NP", "DATA_INSTAURACAO",
                 "DATA_SESSAO", "DATA_PROTOCOLO", "PRAZO_PROTOCOLO")
_CAMPOS_TEXTO = ("RECORRENTE", "TIPO_PENALIDADE", "INFRACAO_DOCUMENTO")


# ── Helpers de parse ────────────────────────────────────────────────────────


def _extrair_campo(texto_resposta: str, label: str) -> str:
    """Extrai valor do campo, removendo o sufixo | CONFIANCA: se presente."""
    m = re.search(rf'{label}:\s*(.+)', texto_resposta, re.IGNORECASE)
    if not m:
        return ""
    val = m.group(1).strip()
    # Remover sufixo de confiança (qualquer variação: com/sem nível, com/sem cedilha)
    val = re.sub(r'\s*\|\s*CONFIAN[CÇ]A\s*:?\s*(ALTA|MEDIA|BAIXA)?\s*$', '', val, flags=re.IGNORECASE)
    return val.strip()


def _extrair_confianca(texto_resposta: str, label: str) -> str:
    """Extrai nível de confiança (ALTA/MEDIA/BAIXA) de um campo."""
    m = re.search(rf'{label}:\s*.+\|\s*CONFIAN[CÇ]A\s*:\s*(ALTA|MEDIA|BAIXA)',
                  texto_resposta, re.IGNORECASE)
    return m.group(1).upper() if m else "MEDIA"


def _parse_date_br(s: str):
    """Tenta parsear data BR DD/MM/AAAA. Retorna date ou None."""
    if not s:
        return None
    s = s.strip().replace("-", "/")
    try:
        return datetime.datetime.strptime(s, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def _parse_resposta_llm(texto_resposta: str) -> dict:
    """
    Parse completo da resposta LLM.
    Retorna dict com valor e confiança por campo:
    {"DATA_INFRACAO": {"valor": "22/01/2022", "confianca": "ALTA"}, ...}
    """
    resultado = {}
    for label in list(_CAMPOS_DATA) + list(_CAMPOS_TEXTO):
        valor = _extrair_campo(texto_resposta, label)
        confianca = _extrair_confianca(texto_resposta, label)
        resultado[label] = {"valor": valor, "confianca": confianca}
    return resultado


# ── Consenso multi-modelo ───────────────────────────────────────────────────


def _consenso(resultado_a: dict, resultado_b: dict, provider_a: str, provider_b: str) -> dict:
    """
    Compara resultados de 2 modelos. Quando concordam → ALTA.
    Quando divergem → rebaixa confiança e loga a divergência.
    Prioriza o resultado com confiança mais alta.
    """
    resultado_final = {}
    divergencias = []

    for campo in list(_CAMPOS_DATA) + list(_CAMPOS_TEXTO):
        a = resultado_a.get(campo, {"valor": "", "confianca": "BAIXA"})
        b = resultado_b.get(campo, {"valor": "", "confianca": "BAIXA"})

        val_a = a["valor"].strip().lower()
        val_b = b["valor"].strip().lower()

        # Normalizar nulos
        is_nulo_a = val_a in _NULOS
        is_nulo_b = val_b in _NULOS

        if is_nulo_a and is_nulo_b:
            # Ambos não encontraram
            resultado_final[campo] = {"valor": "", "confianca": "BAIXA"}
        elif is_nulo_a and not is_nulo_b:
            # Apenas B encontrou
            resultado_final[campo] = {"valor": b["valor"], "confianca": "MEDIA"}
        elif not is_nulo_a and is_nulo_b:
            # Apenas A encontrou
            resultado_final[campo] = {"valor": a["valor"], "confianca": "MEDIA"}
        elif val_a == val_b:
            # Concordam — confiança é a maior dos dois
            melhor = "ALTA" if "ALTA" in (a["confianca"], b["confianca"]) else a["confianca"]
            resultado_final[campo] = {"valor": a["valor"], "confianca": melhor}
        else:
            # Divergem — usar o de maior confiança, rebaixar
            ordem = {"ALTA": 3, "MEDIA": 2, "BAIXA": 1}
            if ordem.get(a["confianca"], 0) >= ordem.get(b["confianca"], 0):
                escolhido = a
                preterido = b
                fonte = provider_a
            else:
                escolhido = b
                preterido = a
                fonte = provider_b

            # Rebaixar confiança por divergência
            nova_confianca = "MEDIA" if escolhido["confianca"] == "ALTA" else "BAIXA"
            resultado_final[campo] = {"valor": escolhido["valor"], "confianca": nova_confianca}
            divergencias.append({
                "campo": campo,
                "valor_escolhido": escolhido["valor"],
                "valor_preterido": preterido["valor"],
                "fonte": fonte,
            })

    if divergencias:
        _log.warning("[CONSENSO] Divergências encontradas: %s", divergencias)

    return resultado_final


# ── Validação cruzada de datas ──────────────────────────────────────────────


def _validar_datas(resultado: dict) -> dict:
    """
    Aplica regras temporais e rebaixa confiança de campos inconsistentes.
    Regras:
    - Todas as datas devem estar entre 2000 e hoje+30 dias
    - data_infracao <= data_na <= data_np
    - data_instauracao >= data_np (geralmente)
    - Intervalos absurdos (>10 anos entre campos) → suspeito
    """
    hoje = datetime.date.today()
    limite_min = datetime.date(2000, 1, 1)
    limite_max = hoje + datetime.timedelta(days=30)

    # Parsear datas
    datas = {}
    for campo in _CAMPOS_DATA:
        info = resultado.get(campo, {})
        parsed = _parse_date_br(info.get("valor", ""))
        datas[campo] = parsed

    alertas = []

    # Regra 1: Datas dentro de range razoável
    # DATA_SESSAO pode ser até 1 ano no futuro (pautas agendadas)
    limite_max_sessao = hoje + datetime.timedelta(days=365)
    for campo, dt in datas.items():
        if not dt:
            continue
        lim = limite_max_sessao if campo == "DATA_SESSAO" else limite_max
        if dt < limite_min or dt > lim:
            alertas.append(f"{campo} fora do range ({dt})")
            resultado[campo]["confianca"] = "BAIXA"

    # Regra 2: Ordem cronológica — infração <= NA <= NP
    di = datas.get("DATA_INFRACAO")
    dna = datas.get("DATA_NA")
    dnp = datas.get("DATA_NP")

    if di and dna and di > dna:
        alertas.append(f"DATA_INFRACAO ({di}) > DATA_NA ({dna})")
        # Rebaixar ambas
        resultado["DATA_INFRACAO"]["confianca"] = _rebaixar(resultado["DATA_INFRACAO"]["confianca"])
        resultado["DATA_NA"]["confianca"] = _rebaixar(resultado["DATA_NA"]["confianca"])

    if dna and dnp and dna > dnp:
        alertas.append(f"DATA_NA ({dna}) > DATA_NP ({dnp})")
        resultado["DATA_NA"]["confianca"] = _rebaixar(resultado["DATA_NA"]["confianca"])
        resultado["DATA_NP"]["confianca"] = _rebaixar(resultado["DATA_NP"]["confianca"])

    if di and dnp and di > dnp:
        alertas.append(f"DATA_INFRACAO ({di}) > DATA_NP ({dnp})")
        resultado["DATA_INFRACAO"]["confianca"] = _rebaixar(resultado["DATA_INFRACAO"]["confianca"])

    # Regra 3: Intervalos absurdos (>10 anos)
    if di and dnp:
        delta = (dnp - di).days
        if delta > 3650:
            alertas.append(f"Intervalo infração→NP = {delta} dias (>10 anos)")
            resultado["DATA_INFRACAO"]["confianca"] = _rebaixar(resultado["DATA_INFRACAO"]["confianca"])
            resultado["DATA_NP"]["confianca"] = _rebaixar(resultado["DATA_NP"]["confianca"])

    if alertas:
        _log.warning("[VALIDACAO_DATAS] Alertas: %s", alertas)

    return resultado


def _rebaixar(confianca: str) -> str:
    """ALTA → MEDIA, MEDIA → BAIXA, BAIXA → BAIXA."""
    if confianca == "ALTA":
        return "MEDIA"
    return "BAIXA"


# ── Salvar campos ──────────────────────────────────────────────────────────


def _salvar_campos(processo, adm, resultado: dict):
    """Salva campos extraídos (já processados por consenso + validação)."""
    recorrente = resultado.get("RECORRENTE", {}).get("valor", "")
    tipo_penalidade = resultado.get("TIPO_PENALIDADE", {}).get("valor", "").lower()
    infracao_doc = resultado.get("INFRACAO_DOCUMENTO", {}).get("valor", "")

    _log.info("[DOCUMENTOS] Salvando campos processo=%s: %s", processo.id, resultado)

    # Salvar no Processo
    processo_update_fields = []
    if recorrente and recorrente.lower() not in _NULOS:
        processo.recorrente = recorrente[:255].upper()
        processo_update_fields.append("recorrente")

    # Datas do Processo (data_sessao, data_protocolo, prazo_final)
    for campo, attr in [
        ("DATA_SESSAO", "data_sessao"),
        ("DATA_PROTOCOLO", "data_protocolo"),
        ("PRAZO_PROTOCOLO", "prazo_final"),
    ]:
        valor = resultado.get(campo, {}).get("valor", "")
        parsed = _parse_date_br(valor)
        if parsed and not getattr(processo, attr):
            setattr(processo, attr, parsed)
            processo_update_fields.append(attr)

    if processo_update_fields:
        processo.save(update_fields=processo_update_fields)

    # Salvar na Admissibilidade
    if tipo_penalidade in ("multa", "advertencia", "suspensao", "cassacao"):
        adm.tipo_penalidade = tipo_penalidade

    if infracao_doc and infracao_doc.lower() not in _NULOS:
        adm.infracao_documento = infracao_doc[:255]

    # Datas
    for campo, attr in [
        ("DATA_INFRACAO", "data_infracao"),
        ("DATA_NA", "data_na"),
        ("DATA_NP", "data_np"),
        ("DATA_INSTAURACAO", "data_instauracao"),
    ]:
        valor = resultado.get(campo, {}).get("valor", "")
        parsed = _parse_date_br(valor)
        if parsed:
            setattr(adm, attr, parsed)

    adm.save()


# ── OCR → Markdown ───────────────────────────────────────────────────────────


def _ocr_para_markdown(ocr_resultado: dict) -> str:
    """
    Converte resultado do Document AI OCR em Markdown estruturado.
    Cada página vira uma seção com heading, facilitando a leitura pela LLM.
    """
    partes = []
    for pagina in ocr_resultado.get("paginas", []):
        num = pagina.get("numero", "?")
        texto = pagina.get("texto", "").strip()
        if texto:
            partes.append(f"## Página {num}\n\n{texto}")

    return "\n\n---\n\n".join(partes)


# ── Providers ────────────────────────────────────────────────────────────────


def _tentar_document_ai(docs_dict: dict) -> dict | None:
    """Tenta OCR via Document AI. Retorna resultado OCR ou None."""
    try:
        from pareceres.integrations.document_ai import DocumentAIClient
        client = DocumentAIClient()
        if not client.disponivel:
            _log.warning("[DOCUMENTOS] Document AI indisponível — client não inicializou")
            return None

        consolidado_path = docs_dict.get("consolidado")
        if not consolidado_path:
            _log.warning("[DOCUMENTOS] Document AI: sem path consolidado")
            return None

        _log.info("[DOCUMENTOS] Document AI: iniciando OCR path=%s", consolidado_path)
        resultado = client.ocr_pdf(consolidado_path)
        if resultado and resultado.get("texto_completo"):
            _log.info("[DOCUMENTOS] Document AI OCR OK: %d páginas, confiança=%.1f%%",
                      resultado["total_paginas"], resultado["confianca_media"] * 100)
        else:
            _log.warning("[DOCUMENTOS] Document AI retornou vazio — resultado=%s", resultado)
        return resultado
    except Exception as e:
        _log.error("[DOCUMENTOS] Document AI falhou: %s", e, exc_info=True)
        return None


def _tentar_gemini(processo, docs_dict: dict, texto_ocr: str | None = None) -> str | None:
    """Tenta extração via Gemini. Se tem texto OCR, usa texto em vez de PDF."""
    try:
        from pareceres.integrations.gemini import GeminiClient
        gemini = GeminiClient()
        if not gemini.client:
            _log.warning("[DOCUMENTOS] Gemini sem API key configurada")
            return None

        start = time.time()

        if texto_ocr:
            # Usar Markdown estruturado do Document AI (mais preciso, sem upload)
            _log.info("[DOCUMENTOS] Gemini com MD processo=%s", processo.id)
            contents = [
                f"# DOCUMENTO DO PROCESSO (OCR Markdown)\n\n{texto_ocr[:50000]}",
                PROMPT_EXTRACAO_F2,
            ]
            response, model_used = gemini.generate(
                model="gemini-2.5-flash",
                contents=contents,
                config={"temperature": 0.1, "max_output_tokens": 1024},
            )
        else:
            # Fallback: upload PDF nativo
            consolidado_path = docs_dict.get("consolidado")
            if not consolidado_path:
                return None

            _log.info("[DOCUMENTOS] Gemini upload PDF processo=%s", processo.id)
            uploaded = gemini.upload_file(consolidado_path)
            if not uploaded:
                _log.error("[DOCUMENTOS] Gemini upload falhou processo=%s", processo.id)
                return None

            _log.info("[DOCUMENTOS] Gemini generate processo=%s", processo.id)
            response, model_used = gemini.generate(
                model="gemini-2.5-flash",
                contents=[uploaded, PROMPT_EXTRACAO_F2],
                config={"temperature": 0.1, "max_output_tokens": 1024},
            )

        texto = response.text if response else None
        gemini.log_usage(processo, response, "Extração F2", model_used, start)

        if texto:
            _log.info("[DOCUMENTOS] Gemini OK processo=%s modelo=%s (ocr=%s)",
                      processo.id, model_used, bool(texto_ocr))
        return texto
    except Exception as e:
        _log.error("[DOCUMENTOS] Gemini falhou: %s", e, exc_info=True)
        return None


def _tentar_anthropic(processo, docs_dict: dict, texto_ocr: str | None = None) -> str | None:
    """Tenta extração via Anthropic. Se tem texto OCR, usa texto em vez de imagens."""
    try:
        from pareceres.integrations.anthropic import AnthropicClient
        client = AnthropicClient()

        if texto_ocr:
            # Usar texto OCR (mais barato, sem converter PDF→JPEG)
            _log.info("[DOCUMENTOS] Anthropic com texto OCR processo=%s", processo.id)
            return client.extract_from_text(processo, texto_ocr, PROMPT_EXTRACAO_F2)

        return client.extract_documentos(processo, docs_dict, PROMPT_EXTRACAO_F2)
    except Exception as e:
        _log.error("[DOCUMENTOS] Anthropic falhou: %s", e)
        return None


# ── Execução principal ───────────────────────────────────────────────────────


def execute(processo) -> ServiceResult:
    """
    Extrai dados do PDF consolidado com pipeline de alta assertividade:
    1. Extração via Gemini (primary) + Claude (secondary)
    2. Consenso multi-modelo (quando ambos disponíveis)
    3. Validação cruzada de datas
    4. Confidence score por campo

    Pré-condição: processo na fase DOCUMENTOS ou DOCUMENTOS_EXTRAINDO.
    Pós-condição: processo na fase DOCUMENTOS_EXTRAIDOS, Admissibilidade criada/atualizada.
    """
    _log.info("[DOCUMENTOS] START processo=%s", processo.id)

    adm, _ = Admissibilidade.objects.get_or_create(processo=processo)

    docs = {d.tipo: d for d in processo.documentos.all()}
    if "consolidado" not in docs:
        return ServiceResult.falha("Documento consolidado não encontrado.")

    docs_dict = {tipo: d.arquivo.name for tipo, d in docs.items() if d.arquivo}

    # ── Camada 0: OCR via Document AI → Markdown ───────────────────────
    ocr_resultado = _tentar_document_ai(docs_dict)
    texto_ocr = None
    if ocr_resultado and ocr_resultado.get("paginas"):
        texto_ocr = _ocr_para_markdown(ocr_resultado)
        _log.info("[DOCUMENTOS] OCR→MD: %d páginas, %d chars, confiança=%.1f%%",
                  ocr_resultado["total_paginas"], len(texto_ocr),
                  ocr_resultado["confianca_media"] * 100)
    elif ocr_resultado and ocr_resultado.get("texto_completo"):
        texto_ocr = ocr_resultado["texto_completo"]
        _log.info("[DOCUMENTOS] OCR texto bruto (sem páginas): %d chars", len(texto_ocr))

    # ── Camada 1: Extração Gemini ───────────────────────────────────────
    texto_gemini = _tentar_gemini(processo, docs_dict, texto_ocr=texto_ocr)
    resultado_gemini = _parse_resposta_llm(texto_gemini) if texto_gemini else None

    # ── Camada 2: Extração Claude (para consenso ou fallback) ───────────
    texto_claude = _tentar_anthropic(processo, docs_dict, texto_ocr=texto_ocr)
    resultado_claude = _parse_resposta_llm(texto_claude) if texto_claude else None

    # ── Camada 3: Consenso ou fallback ──────────────────────────────────
    if resultado_gemini and resultado_claude:
        _log.info("[DOCUMENTOS] Consenso multi-modelo processo=%s", processo.id)
        resultado = _consenso(resultado_gemini, resultado_claude, "Gemini", "Anthropic")
        provider = "Consenso (Gemini+Anthropic)"
    elif resultado_gemini:
        resultado = resultado_gemini
        provider = "Gemini"
    elif resultado_claude:
        resultado = resultado_claude
        provider = "Anthropic"
    else:
        return ServiceResult.falha(
            "Não foi possível processar nenhum documento. "
            "Verifique se os arquivos são PDFs válidos."
        )

    _log.info("[DOCUMENTOS] Resultado pré-validação processo=%s provider=%s: %s",
              processo.id, provider, resultado)

    # ── Camada 4: Validação cruzada de datas ────────────────────────────
    resultado = _validar_datas(resultado)

    # ── Salvar campos e confiança ───────────────────────────────────────
    _salvar_campos(processo, adm, resultado)

    # Montar extracao_json com confiança por campo
    extracao_json = {
        "provider": provider,
        "confianca": {},
    }
    for campo in list(_CAMPOS_DATA) + list(_CAMPOS_TEXTO):
        info = resultado.get(campo, {})
        extracao_json[campo.lower()] = info.get("valor", "")
        extracao_json["confianca"][campo.lower()] = info.get("confianca", "MEDIA")

    # Salvar respostas brutas para auditoria
    if ocr_resultado:
        extracao_json["ocr_confianca"] = ocr_resultado["confianca_media"]
        extracao_json["ocr_paginas"] = ocr_resultado["total_paginas"]
        extracao_json["ocr_latency_ms"] = ocr_resultado["latency_ms"]
    if texto_ocr:
        extracao_json["ocr_markdown"] = texto_ocr
    if texto_gemini:
        extracao_json["raw_gemini"] = texto_gemini[:2000]
    if texto_claude:
        extracao_json["raw_anthropic"] = texto_claude[:2000]

    doc_consolidado = docs.get("consolidado")
    if doc_consolidado:
        doc_consolidado.extracao_json = extracao_json
        doc_consolidado.save(update_fields=["extracao_json"])

    # Avançar fase
    processo.avancar_fase(FaseProcesso.DOCUMENTOS_EXTRAIDOS)

    log_audit("fase", processo=processo, fase="documentos_extraidos",
              dados={"provider": provider, "docs_processados": list(docs_dict.keys()),
                     "confianca": extracao_json.get("confianca", {})})

    _log.info("[DOCUMENTOS] OK processo=%s provider=%s docs=%s",
              processo.id, provider, list(docs_dict.keys()))
    return ServiceResult.sucesso("Documentos extraídos com sucesso.")
