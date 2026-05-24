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


def gerar_texto_prescricao_decadencia(processo, adm, *, usar_flags_julgador=False):
    """
    Gera o bloco determinístico 3.1-3.4 (Prescrição e Decadência).

    Se usar_flags_julgador=True, usa flag_* (decisão efetiva do julgador)
    em vez de has_* (cálculo automático). Chamado após confirmação do julgador.
    """

    data_infracao = adm.data_infracao
    data_na = adm.data_na
    data_np = adm.data_np
    data_instauracao = adm.data_instauracao

    _marcos_semanticos = sorted({
        d for d in [data_na, data_np, data_instauracao]
        if d is not None
        and data_infracao < d < (processo.data_sessao or datetime.date.max)
    })
    marcos_validos = _marcos_semanticos if _marcos_semanticos else []
    ultimo_marco = max(marcos_validos) if marcos_validos else data_infracao

    # COVID-19: desconto proporcional
    _ultimo_marco_punit = max(marcos_validos) if marcos_validos else data_infracao
    if _ultimo_marco_punit and _ultimo_marco_punit <= JariMath.FIM_COVID_SUSPENSAO:
        if _ultimo_marco_punit >= JariMath.INICIO_COVID_SUSPENSAO:
            _desconto_covid = (JariMath.FIM_COVID_SUSPENSAO - _ultimo_marco_punit).days + 1
        else:
            _desconto_covid = JariMath.DIAS_SUSPENSAO_COVID
    else:
        _desconto_covid = 0

    # Flags: usar decisão do julgador ou automática
    if usar_flags_julgador:
        f_punitiva = adm.flag_prescricao_punitiva
        f_intercorrente = adm.flag_prescricao_intercorrente
        f_bienal = adm.flag_prescricao_intercorrente_bienal
        f_decadencia = adm.flag_decadencia
    else:
        f_punitiva = adm.has_prescricao_punitiva
        f_intercorrente = adm.has_prescricao_intercorrente
        f_bienal = adm.has_prescricao_intercorrente_bienal
        f_decadencia = adm.has_decadencia

    def _fmt(d):
        return d.strftime("%d/%m/%Y") if d else "N/A"

    # Aniversários
    _aniv_punitiva = JariMath._aniversario_5_anos(ultimo_marco)
    if _desconto_covid:
        _aniv_punitiva += datetime.timedelta(days=_desconto_covid)

    try:
        _aniv_trienal = processo.data_protocolo.replace(year=processo.data_protocolo.year + 3)
    except (ValueError, AttributeError):
        _aniv_trienal = None

    try:
        _aniv_bienal = processo.data_protocolo.replace(year=processo.data_protocolo.year + 2)
    except (ValueError, AttributeError):
        _aniv_bienal = None

    # 3.1 Prescrição punitiva
    _covid_frase = (
        f", considerando o último ato interruptivo na data de {_fmt(ultimo_marco)}"
        if ultimo_marco != data_infracao else ""
    )
    _covid_desconto = (
        f" O prazo prescricional foi acrescido de {_desconto_covid} dias em razão da "
        "suspensão de prazos pela Resolução CONTRAN 782/2020 (COVID-19)."
        if _desconto_covid else ""
    )
    if f_punitiva:
        texto_31 = (
            f"A prescrição punitiva quinquenal da Lei 9.873/99 ocorreu entre a data da infração "
            f"em {_fmt(data_infracao)} e a presente sessão de julgamento em {_fmt(processo.data_sessao)}. "
            f"O prazo de cinco anos foi ultrapassado{_covid_frase}. "
            f"Configura-se a prescrição punitiva pela superação do prazo legal estabelecido.{_covid_desconto}"
        )
    else:
        texto_31 = (
            f"A prescrição punitiva quinquenal da Lei 9.873/99 não se configurou. "
            f"O intervalo entre a data da infração em {_fmt(data_infracao)} "
            f"e a sessão de julgamento em {_fmt(processo.data_sessao)} "
            f"não ultrapassou o prazo de cinco anos{_covid_frase}.{_covid_desconto}"
        )

    # 3.2 Prescrição intercorrente trienal
    if f_intercorrente:
        texto_32 = (
            f"A prescrição intercorrente trienal da Lei 9.873/99 configurou-se entre o protocolo "
            f"do recurso JARI em {_fmt(processo.data_protocolo)} e a presente sessão de julgamento "
            f"em {_fmt(processo.data_sessao)}. O transcurso de exatos três anos superou o prazo máximo "
            "estabelecido pela legislação federal, extinguindo a pretensão punitiva estatal."
        )
    else:
        texto_32 = (
            f"A prescrição intercorrente trienal da Lei 9.873/99 não se configurou. "
            f"O prazo de três anos é contado a partir da data de protocolo do recurso perante a JARI, "
            f"ocorrida em {_fmt(processo.data_protocolo)}. A sessão de julgamento ocorreu em "
            f"{_fmt(processo.data_sessao)}, data anterior ao término do triênio, que somente se "
            f"completaria em {_fmt(_aniv_trienal)}."
        )

    # 3.3 Prescrição intercorrente bienal
    # Safety lock: protocolo anterior a 01/01/2024 → não se aplica (independente da flag)
    _bienal_nao_se_aplica = (
        processo.data_protocolo and processo.data_protocolo < datetime.date(2024, 1, 1)
    )

    if _bienal_nao_se_aplica:
        texto_33 = (
            "A prescrição intercorrente bienal do artigo 285, parágrafo 6º, combinado com o artigo "
            "289-A do Código de Trânsito Brasileiro não se aplica ao presente caso. O protocolo do "
            f"recurso ocorreu em {_fmt(processo.data_protocolo)}, anteriormente a 01/01/2024, data a "
            "partir da qual a Lei 14.229/2021 passou a produzir efeitos para fins de contagem do prazo "
            "bienal. Análise prejudicada."
        )
    elif f_bienal:
        texto_33 = (
            f"A prescrição intercorrente bienal do artigo 285, parágrafo 6º, combinado com o artigo "
            f"289-A do Código de Trânsito Brasileiro configurou-se pelo transcurso de dois anos "
            f"entre o protocolo do recurso em {_fmt(processo.data_protocolo)} e a sessão de julgamento "
            f"em {_fmt(processo.data_sessao)}. A Lei 14.229/2021 estabeleceu prazo bienal para julgamento "
            "dos recursos, sendo esta prescrição matéria de ordem pública que deve ser reconhecida de ofício."
        )
    else:
        texto_33 = (
            f"A prescrição intercorrente bienal do artigo 285, parágrafo 6º, combinado com o artigo "
            f"289-A do Código de Trânsito Brasileiro não se configurou. O protocolo do recurso ocorreu "
            f"em {_fmt(processo.data_protocolo)} e a sessão de julgamento em {_fmt(processo.data_sessao)}, "
            f"dentro do prazo bienal que se completaria em {_fmt(_aniv_bienal)}."
        )

    # 3.4 Decadência
    # Determinar regime: infração < 12/04/2021 → vedada retroatividade
    _antes_contran = data_infracao and data_infracao < datetime.date(2021, 4, 12)
    _susp_cassacao = (
        adm.tipo_penalidade and adm.tipo_penalidade.lower() in ("suspensao", "cassacao")
        and data_infracao and data_infracao < datetime.date(2021, 10, 22)
    )

    if _antes_contran and not f_decadencia:
        texto_34 = (
            f"A infração ocorreu em {_fmt(data_infracao)}, anteriormente à vigência da "
            "Resolução CONTRAN 844/2021. Conforme Parecer CETRAN/SC 381/2022, os prazos "
            "decadenciais não se aplicam às infrações anteriores à vigência normativa, vedada a "
            "aplicação retroativa. A análise foi encaminhada exclusivamente à Prescrição Punitiva "
            "(Lei 9.873/1999)."
        )
    elif _susp_cassacao and not f_decadencia:
        texto_34 = (
            f"A infração ocorreu em {_fmt(data_infracao)}, no período de transição normativa. "
            "Conforme Nota CETRAN/SC 02/03/2023, a decadência de 180/360 dias restringe-se "
            "exclusivamente a multas e advertências neste período, não se aplicando a penalidades "
            "de suspensão ou cassação. A análise foi encaminhada à Prescrição Punitiva (Lei 9.873/1999)."
        )
    elif f_decadencia:
        texto_34 = (
            f"A decadência configurou-se no presente caso. A infração ocorreu em {_fmt(data_infracao)}."
        )
    else:
        texto_34 = (
            f"A decadência não se configurou no presente caso. A infração ocorreu em {_fmt(data_infracao)}."
        )

    return (
        f"**3.1 Prescrição punitiva**\n\n{texto_31}\n\n"
        f"**3.2 Prescrição intercorrente trienal**\n\n{texto_32}\n\n"
        f"**3.3 Prescrição intercorrente bienal**\n\n{texto_33}\n\n"
        f"**3.4 Decadência**\n\n{texto_34}"
    )


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

    punitiva_bool, relatorio_punitiva = JariMath.check_prescription_punitiva(
        data_inicio_punitiva, processo.data_sessao,
        marcos_validos or None,
        desconto_covid_dias=_desconto_covid,
    )
    adm.has_prescricao_punitiva = punitiva_bool

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

    if "NÃO SE APLICA" in relatorio_decad:
        decadencia_final = "NÃO SE APLICA"
    elif adm.has_decadencia:
        decadencia_final = "SIM"
    else:
        decadencia_final = "NÃO"

    # ── Fundamentações individuais por critério (para Fase 3 / UI) ────────
    fund_tempestivo = (
        f"Diferença em dias corridos = {dias_tempestividade} dias. "
        f"Prazo protocolo: {processo.prazo_final}. Data protocolo: {processo.data_protocolo}. "
        f"Conclusão: {'RECURSO TEMPESTIVO' if adm.is_tempestivo else 'RECURSO INTEMPESTIVO'}."
    )

    adm.fundamentacoes = {
        "tempestivo": fund_tempestivo,
        "punitiva": relatorio_punitiva,
        "intercorrente": relatorio_inter,
        "bienal": relatorio_inter_bienal,
        "decadencia": relatorio_decad,
    }

    matematica_detalhes = (
        f"- Tempestividade: {fund_tempestivo}\n"
        f"- Prescrição Punitiva: {relatorio_punitiva}\n"
        f"- Prescrição Intercorrente (Trienal): {relatorio_inter}\n"
        f"- Prescrição Intercorrente Bienal: {relatorio_inter_bienal}\n"
        f"- Decadência:\n{relatorio_decad}\n"
    )

    # ── Textos determinísticos para seções 3.1-3.4 do Parecer (Fase 5) ───
    adm.fundamentacoes["texto_prescricao_decadencia"] = gerar_texto_prescricao_decadencia(
        processo, adm, usar_flags_julgador=False,
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

    _log.info("[ADMISSIBILIDADE] JariMath OK, chamando Anthropic... processo=%s", processo.id)
    anthropic = AnthropicClient()
    texto_ia = anthropic.generate_text(
        processo, prompt_user,
        system_prompt=SYSTEM_INSTRUCTION,
        max_tokens=4096,
        temperature=0.2,
        fase_label="Admissibilidade F3",
    )
    _log.info("[ADMISSIBILIDADE] Anthropic retornou (has_text=%s) processo=%s",
              bool(texto_ia), processo.id)

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
    def _fmt_tab(d):
        return d.strftime("%d/%m/%Y") if d else "Não localizada"

    _linhas_tabela = [
        "| Tipo | Data | Origem |",
        "|---|---|---|",
        f"| Data da Infração | {_fmt_tab(data_infracao)} | AIT / Documentos |",
        f"| Notificação de Autuação (NA) | {_fmt_tab(data_na)} | Documentos |",
        f"| Notificação de Penalidade (NP) | {_fmt_tab(data_np)} | Documentos |",
        f"| Instauração do Processo | {_fmt_tab(data_instauracao)} | Documentos |",
        f"| Protocolo Recurso JARI | {_fmt_tab(processo.data_protocolo)} | Informado pelo MJ |",
        f"| Prazo Final Interposição | {_fmt_tab(processo.prazo_final)} | Informado pelo MJ |",
        f"| Data da Sessão JARI | {_fmt_tab(processo.data_sessao)} | Informado pelo MJ |",
    ]
    if _data_totalizacao:
        _linhas_tabela.append(
            f"| Totalização de Pontos | {_fmt_tab(_data_totalizacao)} | Informado pelo MJ |"
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
