"""
Service Fase 3 — Cálculos de admissibilidade (JariMath) + geração de texto via Anthropic.

Responsabilidades:
1. Ler datas preenchidas pelo MJ nos campos da Admissibilidade
2. Cálculos matemáticos via JariMath (tempestividade, prescrição, decadência)
3. Geração do texto de admissibilidade via Anthropic (Claude)
4. Avançar para ADMISSIBILIDADE_AGUARDANDO
"""

import datetime
import logging

from pareceres.estado import FaseProcesso
from pareceres.math import JariMath
from pareceres.models import log_audit
from . import ServiceResult

_log = logging.getLogger(__name__)


def execute(processo) -> ServiceResult:
    """
    Calcula admissibilidade: JariMath + Anthropic (Claude).

    Pré-condição: processo na fase ADMISSIBILIDADE, Admissibilidade com data_infracao preenchida.
    Pós-condição: processo na fase ADMISSIBILIDADE_AGUARDANDO, flags calculadas.
    """
    from pareceres.integrations.anthropic import AnthropicClient
    from pareceres.prompts.phase_3 import SYSTEM_INSTRUCTION

    try:
        adm = processo.admissibilidade
    except Exception:
        return ServiceResult.falha("Admissibilidade não encontrada. Execute a extração de documentos primeiro.")

    # ── Datas preenchidas pelo MJ ────────────────────────────────────────
    data_infracao = adm.data_infracao
    if not data_infracao:
        return ServiceResult.falha(
            "Data da Infração não preenchida. "
            "Preencha a data no Passo 2 antes de prosseguir."
        )

    _log.info("[ADMISSIBILIDADE] START processo=%s", processo.id)

    data_na = adm.data_na
    data_np = adm.data_np
    data_instauracao = adm.data_instauracao

    data_notificacao_autuacao = (
        data_np or data_na or data_infracao
    )

    _marcos_semanticos = sorted({
        d for d in [data_na, data_np, data_instauracao]
        if d is not None
        and data_infracao < d < (processo.data_sessao or datetime.date.max)
    })

    # ── Cálculos JariMath ─────────────────────────────────────────────────

    # Tempestividade
    adm.is_tempestivo = JariMath.check_tempestividade(processo.data_protocolo, processo.prazo_final)

    # Marco inicial da prescrição punitiva
    _data_totalizacao = adm.data_totalizacao_pontos
    _e_suspensao_pontos = (
        adm.tipo_penalidade == "suspensao" and _data_totalizacao is not None
    )
    if _e_suspensao_pontos:
        data_inicio_punitiva = _data_totalizacao + datetime.timedelta(days=1)
    else:
        data_inicio_punitiva = data_infracao

    marcos_validos = _marcos_semanticos if _marcos_semanticos else []

    # COVID-19: desconto proporcional
    _ultimo_marco_punit = max(marcos_validos) if marcos_validos else data_inicio_punitiva
    if _ultimo_marco_punit and _ultimo_marco_punit <= JariMath.FIM_COVID_SUSPENSAO:
        if _ultimo_marco_punit >= JariMath.INICIO_COVID_SUSPENSAO:
            _desconto_covid = (JariMath.FIM_COVID_SUSPENSAO - _ultimo_marco_punit).days + 1
        else:
            _desconto_covid = JariMath.DIAS_SUSPENSAO_COVID
    else:
        _desconto_covid = 0

    adm.has_prescricao_punitiva = JariMath.check_prescription_punitiva(
        data_inicio_punitiva, processo.data_sessao,
        marcos_validos or None,
        desconto_covid_dias=_desconto_covid,
    )

    # Prescrição intercorrente (trienal — Lei 9.873/99)
    inter_bool, relatorio_inter = JariMath.check_prescription_intercorrente(
        processo.data_protocolo, processo.data_sessao
    )
    adm.has_prescricao_intercorrente = inter_bool

    # Prescrição intercorrente bienal (art. 285, § 6º, c/c art. 289-A do CTB)
    inter_bienal_bool, relatorio_inter_bienal = JariMath.check_prescription_intercorrente_bienal(
        processo.data_protocolo, processo.data_sessao
    )
    adm.has_prescricao_intercorrente_bienal = inter_bienal_bool

    # Decadência
    decad_bool, relatorio_decad = JariMath.check_decadencia(
        data_infracao,
        data_notificacao_autuacao,
        processo.data_sessao,
        tipo_penalidade=adm.tipo_penalidade,
        data_conclusao_multa=adm.data_conclusao_multa,
        tem_flagrante=adm.tem_flagrante,
        data_conhecimento_infracao=adm.data_conhecimento_infracao,
    )
    adm.has_decadencia = decad_bool

    _log.info(
        "[ADMISSIBILIDADE] resultado | processo=%s | tempestivo=%s | punitiva=%s | "
        "intercorrente=%s | intercorrente_bienal=%s | decadencia=%s | data_infracao=%s",
        processo.id, adm.is_tempestivo, adm.has_prescricao_punitiva,
        adm.has_prescricao_intercorrente, adm.has_prescricao_intercorrente_bienal,
        adm.has_decadencia, data_infracao,
    )

    # ── Resumo matemático ─────────────────────────────────────────────────
    dias_tempestividade = 0
    if processo.prazo_final and processo.data_protocolo:
        dias_tempestividade = JariMath.calculate_days_diff(processo.prazo_final, processo.data_protocolo)

    ultimo_marco = max(marcos_validos) if marcos_validos else data_infracao
    dias_punitiva = 0
    if processo.data_sessao:
        dias_punitiva = JariMath.calculate_days_diff(ultimo_marco, processo.data_sessao)

    if "NÃO SE APLICA" in relatorio_decad:
        decadencia_final = "NÃO SE APLICA"
    elif adm.has_decadencia:
        decadencia_final = "SIM"
    else:
        decadencia_final = "NÃO"

    # ── Fundamentações individuais por critério ────────────────────────────
    fund_tempestivo = (
        f"Diferença em dias corridos = {dias_tempestividade} dias. "
        f"Prazo protocolo: {processo.prazo_final}. Data protocolo: {processo.data_protocolo}. "
        f"Conclusão: {'RECURSO TEMPESTIVO' if adm.is_tempestivo else 'RECURSO INTEMPESTIVO'}."
    )
    fund_punitiva = (
        f"Marco inicial = {data_inicio_punitiva}. "
        f"Último marco interruptivo = {ultimo_marco}. "
        f"Intervalo até sessão ({processo.data_sessao}) = {dias_punitiva} dias. "
        f"Prazo legal: 5 anos (Lei 9.873/99). "
        f"{'Desconto COVID: ' + str(_desconto_covid) + ' dias. ' if _desconto_covid else ''}"
        f"Valor calculado: {'SIM (prescrito)' if adm.has_prescricao_punitiva else 'NÃO'}."
    )
    fund_intercorrente = (
        f"{relatorio_inter} "
        f"Protocolo JARI: {processo.data_protocolo}. Sessão: {processo.data_sessao}. "
        f"Prazo legal: 3 anos (Lei 9.873/99). "
        f"Valor calculado: {'SIM (prescrito)' if adm.has_prescricao_intercorrente else 'NÃO'}."
    )
    fund_bienal = (
        f"{relatorio_inter_bienal} "
        f"Protocolo JARI: {processo.data_protocolo}. Sessão: {processo.data_sessao}. "
        f"Prazo legal: 2 anos (art. 285 §6º c/c art. 289-A CTB — Lei 14.229/2021). "
        f"Valor calculado: {'SIM (prescrito)' if adm.has_prescricao_intercorrente_bienal else 'NÃO'}."
    )
    fund_decadencia = (
        f"{relatorio_decad} "
        f"Valor calculado: {decadencia_final}."
    )

    adm.fundamentacoes = {
        "tempestivo": fund_tempestivo,
        "punitiva": fund_punitiva,
        "intercorrente": fund_intercorrente,
        "bienal": fund_bienal,
        "decadencia": fund_decadencia,
    }

    matematica_detalhes = (
        f"- Tempestividade: {fund_tempestivo}\n"
        f"- Prescrição Punitiva: {fund_punitiva}\n"
        f"- Prescrição Intercorrente (Trienal): {fund_intercorrente}\n"
        f"- Prescrição Intercorrente Bienal: {fund_bienal}\n"
        f"- Decadência: {fund_decadencia}\n"
    )

    # ── Geração do texto via Anthropic (Claude) ─────────────────────────
    datas_resumo = (
        f"- Data da Infração: {data_infracao}\n"
        f"- Data NA: {data_na or 'N/A'}\n"
        f"- Data NP: {data_np or 'N/A'}\n"
        f"- Data Instauração: {data_instauracao or 'N/A'}\n"
        f"- Data da Sessão: {processo.data_sessao}\n"
        f"- Data do Protocolo: {processo.data_protocolo}\n"
        f"- Prazo Final: {processo.prazo_final}\n"
    )

    prompt_user = (
        f"DATAS DO PROCESSO:\n{datas_resumo}\n\n"
        f"CÁLCULOS MATEMÁTICOS (Python — NÃO RECALCULE):\n{matematica_detalhes}\n\n"
        f"Gere o RESULTADO FINAL conforme as regras do SYSTEM."
    )

    anthropic = AnthropicClient()
    texto_ia = anthropic.generate_text(
        processo, prompt_user,
        system_prompt=SYSTEM_INSTRUCTION,
        max_tokens=4096,
        temperature=0.2,
        fase_label="Admissibilidade F3",
    )

    if texto_ia:
        adm.texto_resultado = texto_ia
    else:
        _log.warning("[ADMISSIBILIDADE] Anthropic indisponível — processo=%s. "
                     "Prosseguindo com cálculos matemáticos.", processo.id)
        adm.texto_resultado = (
            "Texto explicativo indisponível (falha na IA).\n\n"
            f"CÁLCULOS MATEMÁTICOS:\n{matematica_detalhes}"
        )

    # ── Tabela de datas sensíveis (Markdown) ─────────────────────────────
    def _fmt(d):
        return d.strftime("%d/%m/%Y") if d else "Não localizada"

    _linhas_tabela = [
        "| Tipo | Data | Origem |",
        "|---|---|---|",
        f"| Data da Infração | {_fmt(data_infracao)} | AIT / Documentos |",
        f"| Notificação de Autuação (NA) | {_fmt(data_na)} | Documentos |",
        f"| Notificação de Penalidade (NP) | {_fmt(data_np)} | Documentos |",
        f"| Instauração do Processo | {_fmt(data_instauracao)} | Documentos |",
        f"| Protocolo Recurso JARI | {_fmt(processo.data_protocolo)} | Informado pelo MJ |",
        f"| Prazo Final Interposição | {_fmt(processo.prazo_final)} | Informado pelo MJ |",
        f"| Data da Sessão JARI | {_fmt(processo.data_sessao)} | Informado pelo MJ |",
    ]
    if _data_totalizacao:
        _linhas_tabela.append(
            f"| Totalização de Pontos | {_fmt(_data_totalizacao)} | Informado pelo MJ |"
        )
    adm.tabela_datas_sensiveis = "\n".join(_linhas_tabela)

    adm.save()

    # Avançar fase
    processo.avancar_fase(FaseProcesso.ADMISSIBILIDADE_AGUARDANDO)

    log_audit("fase", processo=processo, fase="admissibilidade_calculada", dados={
        "tempestivo": adm.is_tempestivo,
        "punitiva": adm.has_prescricao_punitiva,
        "intercorrente": adm.has_prescricao_intercorrente,
        "intercorrente_bienal": adm.has_prescricao_intercorrente_bienal,
        "decadencia": adm.has_decadencia,
    })

    _log.info("[ADMISSIBILIDADE] OK processo=%s", processo.id)
    return ServiceResult.sucesso(
        "Admissibilidade calculada.",
        texto=adm.texto_resultado,
    )
