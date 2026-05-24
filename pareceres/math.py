"""
Cálculos determinísticos de prazos jurídicos — tempestividade, prescrição e decadência.

LLMs NUNCA computam datas. Este módulo é a única fonte de verdade para cálculos temporais.
Portado ipsis literis de P-Jari_antigo/chat/jari_math.py.
"""

import calendar
import datetime
import logging

_logger = logging.getLogger(__name__)


class JariMath:
    # Res. CONTRAN 782/2020 — suspensão de prazos COVID-19
    # logica_jari.md §246: "A partir de 20/03/2020... encerramento em 30/11/2020, totalizando 256 dias corridos."
    INICIO_COVID_SUSPENSAO = datetime.date(2020, 3, 20)
    FIM_COVID_SUSPENSAO = datetime.date(2020, 11, 30)
    # Período inclusivo (20/03 e 30/11 contam): (FIM - INICIO).days + 1 = 256
    DIAS_SUSPENSAO_COVID = (FIM_COVID_SUSPENSAO - INICIO_COVID_SUSPENSAO).days + 1  # 256

    @staticmethod
    def is_leap_year(year):
        """Verifica se um ano é bissexto."""
        return calendar.isleap(year)

    @staticmethod
    def count_leap_years(start_year, end_year):
        """Conta quantos anos bissextos existem em um intervalo."""
        count = 0
        for year in range(start_year, end_year + 1):
            if calendar.isleap(year):
                count += 1
        return count

    @staticmethod
    def _parse_date(value):
        """Converte string ISO para date, ou retorna o date original."""
        if isinstance(value, str):
            return datetime.datetime.strptime(value, "%Y-%m-%d").date()
        return value

    @staticmethod
    def calculate_days_diff(start_date, end_date):
        """
        Calcula a diferença em dias corridos.
        Exclui o dia inicial e inclui o dia final na contagem.
        Matematicamente, (data_final - data_inicial).days já faz exatamente isso.
        """
        if not start_date or not end_date:
            return 0

        start_date = JariMath._parse_date(start_date)
        end_date = JariMath._parse_date(end_date)
        return (end_date - start_date).days

    @staticmethod
    def _aniversario_5_anos(data_base):
        """
        Calcula a data exata do aniversário de 5 anos de data_base (mesmo dia/mês).
        Trata 29/02 em anos não-bissextos: usa 01/03 como aniversário.
        Spec logica_jari.md §119: "somar exatos 5 anos, mantendo mesmo dia/mês".
        """
        ano_aniversario = data_base.year + 5
        dia = data_base.day
        mes = data_base.month
        # 29/02 em ano não-bissexto → 01/03: preserva os 5 anos completos
        if mes == 2 and dia == 29 and not calendar.isleap(ano_aniversario):
            mes = 3
            dia = 1
        return data_base.replace(year=ano_aniversario, month=mes, day=dia)

    @staticmethod
    def check_prescription_punitiva(data_infracao, data_sessao, marcos_interruptivos=None, desconto_covid_dias=0):
        """
        Prescrição Punitiva (Lei 9.873/99): 5 anos por data aniversário.

        - A cada ato interruptivo válido, reinicia a contagem do zero.
        - O prazo expira às 23:59 do dia aniversário de 5 anos do último marco.
        - Se data_sessao > aniversário → SIM (prescrito).
        - Se data_sessao <= aniversário → NÃO.

        desconto_covid_dias: dias de suspensão COVID (256) a adicionar ao aniversário
          prescricional quando o último marco ocorreu antes do fim do período COVID.
        """
        ultimo_marco = data_infracao
        if marcos_interruptivos and len(marcos_interruptivos) > 0:
            ultimo_marco = max(marcos_interruptivos)

        aniversario = JariMath._aniversario_5_anos(ultimo_marco)
        if desconto_covid_dias:
            aniversario = aniversario + datetime.timedelta(days=desconto_covid_dias)

        _prescrito = data_sessao > aniversario if data_sessao else False

        # Relatório determinístico
        intervalo = (data_sessao - ultimo_marco).days if data_sessao else 0
        covid_txt = f" Desconto COVID: {desconto_covid_dias} dias." if desconto_covid_dias else ""
        relatorio = (
            f"Marco inicial = {data_infracao.strftime('%d/%m/%Y')}. "
            f"Último marco interruptivo = {ultimo_marco.strftime('%d/%m/%Y')}. "
            f"Intervalo até sessão ({data_sessao.strftime('%d/%m/%Y') if data_sessao else 'N/A'}) = {intervalo} dias. "
            f"Prazo legal: 5 anos (Lei 9.873/99).{covid_txt} "
            f"Valor calculado: {'SIM' if _prescrito else 'NÃO'}."
        )

        _logger.info(
            "[JARIMATH] punitiva=%s | ultimo_marco=%s | aniversario=%s | sessao=%s | covid_dias=%s",
            _prescrito, ultimo_marco, aniversario, data_sessao, desconto_covid_dias,
        )
        return _prescrito, relatorio

    @staticmethod
    def check_prescription_intercorrente(data_protocolo, data_sessao):
        """
        Prescrição Intercorrente (Lei 9.873/99).
        Prazo legal: 3 anos. Contagem: Calendário Civil (data a data).
        Datas obrigatórias exclusivas: Protocolo JARI (F1/P5) e Sessão (F1/P1).

        Se data_sessao <= aniversário de 3 anos → NÃO configurada.
        Se data_sessao > aniversário de 3 anos → SIM configurada.
        """
        if not data_protocolo or not data_sessao:
            return False, "Dados insuficientes para calcular a prescrição intercorrente."

        data_protocolo = JariMath._parse_date(data_protocolo)
        data_sessao = JariMath._parse_date(data_sessao)

        # Aniversário de 3 anos (Calendário Civil)
        try:
            aniversario = data_protocolo.replace(year=data_protocolo.year + 3)
        except ValueError:
            # 29/02 em ano não-bissexto → 28/02
            aniversario = data_protocolo.replace(year=data_protocolo.year + 3, day=28)

        is_prescrito = data_sessao > aniversario

        if is_prescrito:
            declaracao = "Prescrição intercorrente configurada."
        else:
            declaracao = "Prescrição intercorrente não configurada."

        _logger.info(
            "[JARIMATH] intercorrente=%s | protocolo=%s | aniversario=%s | sessao=%s",
            is_prescrito, data_protocolo, aniversario, data_sessao,
        )
        return is_prescrito, declaracao

    @staticmethod
    def check_prescription_intercorrente_bienal(data_protocolo, data_sessao):
        """
        Prescrição Intercorrente Bienal (art. 285, § 6º, c/c art. 289-A do CTB).
        Prazo legal: 2 anos. Contagem: Calendário Civil (data a data).
        Datas obrigatórias exclusivas: Protocolo JARI (F1/P5) e Sessão (F1/P1).

        Se data_sessao <= aniversário de 2 anos → NÃO configurada.
        Se data_sessao > aniversário de 2 anos → SIM configurada.
        """
        if not data_protocolo or not data_sessao:
            return False, "Dados insuficientes para calcular a prescrição intercorrente bienal."

        data_protocolo = JariMath._parse_date(data_protocolo)
        data_sessao = JariMath._parse_date(data_sessao)

        # Trava de segurança: art. 285, §6º c/c art. 289-A do CTB (Lei 14.229/2021)
        # Aplica-se somente a protocolos a partir de 01/01/2024.
        if data_protocolo < datetime.date(2024, 1, 1):
            _logger.info(
                "[JARIMATH] intercorrente_bienal=NAO_SE_APLICA | protocolo=%s (anterior a 01/01/2024)",
                data_protocolo,
            )
            return False, "NÃO SE APLICA — protocolo anterior a 01/01/2024."

        # Aniversário de 2 anos (Calendário Civil)
        try:
            aniversario = data_protocolo.replace(year=data_protocolo.year + 2)
        except ValueError:
            # 29/02 em ano não-bissexto → 28/02
            aniversario = data_protocolo.replace(year=data_protocolo.year + 2, day=28)

        is_prescrito = data_sessao > aniversario

        if is_prescrito:
            declaracao = "Prescrição intercorrente bienal configurada."
        else:
            declaracao = "Prescrição intercorrente bienal não configurada."

        _logger.info(
            "[JARIMATH] intercorrente_bienal=%s | protocolo=%s | aniversario=%s | sessao=%s",
            is_prescrito, data_protocolo, aniversario, data_sessao,
        )
        return is_prescrito, declaracao

    @staticmethod
    def check_decadencia(data_infracao, data_expedicao_autuacao, data_decisao_final=None,
                         tipo_penalidade=None, data_conclusao_multa=None,
                         tem_flagrante=None, data_conhecimento_infracao=None):
        """
        Decadência CTB (Fase 3):
        Avalia Faixa Temporal (FILTRO 1/2/3), Regra Aplicada e Incidência COVID.

        tipo_penalidade: 'multa' | 'advertencia' | 'suspensao' | 'cassacao'
        data_conclusao_multa: conclusão do processo de multa (FILTRO 3 suspensão/cassação)
        tem_flagrante: True=flagrante, False=sem flagrante, None=não determinado
        data_conhecimento_infracao: data de ciência do órgão (sem flagrante)

        Retorna: (bool, str) — (decadência configurada?, relatório)
        """
        data_infracao = JariMath._parse_date(data_infracao)
        data_expedicao_autuacao = JariMath._parse_date(data_expedicao_autuacao)
        if data_decisao_final:
            data_decisao_final = JariMath._parse_date(data_decisao_final)

        # Marcos legais de transição CTB
        LIMIAR_1_ANTIGA = datetime.date(2021, 4, 12)
        LIMIAR_2_TRANSICAO = datetime.date(2021, 10, 22)

        dias_infracao_notificacao = JariMath.calculate_days_diff(data_infracao, data_expedicao_autuacao)
        desconto_covid = 0
        incidencia_covid_texto = "Não aplicável."

        if data_infracao <= JariMath.FIM_COVID_SUSPENSAO:
            if data_infracao >= JariMath.INICIO_COVID_SUSPENSAO:
                desconto_covid = (JariMath.FIM_COVID_SUSPENSAO - data_infracao).days + 1
            else:
                desconto_covid = JariMath.DIAS_SUSPENSAO_COVID
            dias_infracao_notificacao = max(0, dias_infracao_notificacao - desconto_covid)
            incidencia_covid_texto = f"Sim (Res. 782/CONTRAN gerou desconto de -{desconto_covid} dias ao cômputo)."

        faixa_temporal = ""
        regra_aplicada = ""
        decadencia_encontrada = False
        detalhe_calculo = ""

        _penalidade_grave = tipo_penalidade and tipo_penalidade.lower() in ("suspensao", "cassacao")

        # ── FILTRO 3: a partir de 22/10/2021 ──
        if data_infracao >= LIMIAR_2_TRANSICAO:
            faixa_temporal = "Após 22/10/2021"

            if _penalidade_grave:
                regra_aplicada = "360 dias Instauração — marco: conclusão da multa (art. 24 §1º Res. 844/2021)"
                if data_conclusao_multa:
                    data_marco_inicio = JariMath._parse_date(data_conclusao_multa)
                    dias_marco_notificacao = max(
                        0, JariMath.calculate_days_diff(data_marco_inicio, data_expedicao_autuacao) - desconto_covid
                    )
                else:
                    dias_marco_notificacao = dias_infracao_notificacao
                    regra_aplicada += " [AVISO: data_conclusao_multa não informada — usando data_infracao como fallback]"

                if dias_marco_notificacao > 360:
                    decadencia_encontrada = True
                    detalhe_calculo = (
                        f"Instauração excedeu 360 dias a partir da conclusão da multa "
                        f"({dias_marco_notificacao} dias contabilizados)."
                    )
                else:
                    detalhe_calculo = (
                        f"Dentro do limite de 360 dias a partir da conclusão da multa "
                        f"({dias_marco_notificacao} dias transcorridos)."
                    )
            else:
                if tem_flagrante is False and data_conhecimento_infracao:
                    data_marco_multa = JariMath._parse_date(data_conhecimento_infracao)
                    regra_aplicada = "360 dias — marco: data do conhecimento (sem flagrante, art. 282 §6º-A CTB)"
                    dias_marco_notificacao = max(
                        0, JariMath.calculate_days_diff(data_marco_multa, data_expedicao_autuacao) - desconto_covid
                    )
                    limiar_f3 = 360
                elif tem_flagrante is False and not data_conhecimento_infracao:
                    regra_aplicada = "180 dias — sem flagrante, marco data_infracao (data_conhecimento não informada)"
                    dias_marco_notificacao = dias_infracao_notificacao
                    limiar_f3 = 180
                else:
                    regra_aplicada = "180 dias — marco: data da infração (flagrante, art. 282 §6º-A CTB)"
                    if tem_flagrante is None:
                        regra_aplicada += " [flagrante não determinado — usando data_infracao como fallback]"
                    dias_marco_notificacao = dias_infracao_notificacao
                    limiar_f3 = 180

                if dias_marco_notificacao > limiar_f3:
                    decadencia_encontrada = True
                    detalhe_calculo = f"Prazo excedeu {limiar_f3} dias ({dias_marco_notificacao} dias contabilizados)."
                else:
                    detalhe_calculo = f"Dentro do limite de {limiar_f3} dias ({dias_marco_notificacao} dias transcorridos)."

        # ── FILTRO 2: 12/04/2021 a 21/10/2021 ──
        elif data_infracao >= LIMIAR_1_ANTIGA and data_infracao < LIMIAR_2_TRANSICAO:
            faixa_temporal = "De 12/04/2021 a 21/10/2021"

            if _penalidade_grave:
                faixa_temporal = "De 12/04/2021 a 22/10/2021 — Suspensão/Cassação"
                regra_aplicada = "NÃO SE APLICA — Suspensão/Cassação (Nota CETRAN/SC 02/03/2023)"
                decadencia_encontrada = False
                detalhe_calculo = (
                    "Penalidade de suspensão ou cassação com infração no período de 12/04/2021 a 22/10/2021. "
                    "A Nota CETRAN/SC 02/03/2023 restringe a decadência de 180/360 dias "
                    "exclusivamente a multas e advertências neste período. "
                    "Análise encaminhada à Prescrição Punitiva (Lei 9.873/1999)."
                )
            else:
                if tem_flagrante is False and data_conhecimento_infracao:
                    data_marco_multa_f2 = JariMath._parse_date(data_conhecimento_infracao)
                    regra_aplicada = "360 dias — marco: data do conhecimento (sem flagrante, art. 282 §6º-A CTB)"
                    dias_marco_f2 = max(
                        0, JariMath.calculate_days_diff(data_marco_multa_f2, data_expedicao_autuacao) - desconto_covid
                    )
                    limiar_f2 = 360
                elif tem_flagrante is False and not data_conhecimento_infracao:
                    regra_aplicada = "180 dias — sem flagrante, marco data_infracao (data_conhecimento não informada)"
                    dias_marco_f2 = dias_infracao_notificacao
                    limiar_f2 = 180
                else:
                    regra_aplicada = "180 dias Notificação / 360 dias Decisão Final"
                    if tem_flagrante is None:
                        regra_aplicada += " [flagrante não determinado — usando data_infracao como fallback]"
                    dias_marco_f2 = dias_infracao_notificacao
                    limiar_f2 = 180

                if dias_marco_f2 > limiar_f2:
                    decadencia_encontrada = True
                    detalhe_calculo = f"Prazo excedeu {limiar_f2} dias ({dias_marco_f2} dias contabilizados)."
                else:
                    detalhe_calculo = f"Dentro do limite de {limiar_f2} dias ({dias_marco_f2} dias transcorridos)."
                    if data_decisao_final and limiar_f2 == 180:
                        dias_decisao_f2 = max(
                            0, JariMath.calculate_days_diff(data_infracao, data_decisao_final) - desconto_covid
                        )
                        if dias_decisao_f2 > 360:
                            decadencia_encontrada = True
                            detalhe_calculo = (
                                f"Decisão final excedeu 360 dias da infração "
                                f"({dias_decisao_f2} dias contabilizados)."
                            )

        # ── FILTRO 1: antes de 12/04/2021 — HARD STOP ──
        else:
            faixa_temporal = "Antes 12/04/2021"
            regra_aplicada = "NÃO SE APLICA — Blindagem CETRAN/SC 381/2022"
            decadencia_encontrada = False
            detalhe_calculo = (
                "Infração anterior a 12/04/2021. "
                "Decadência de 180/360 dias expressamente proibida pelo Parecer CETRAN/SC 381/2022. "
                "Análise encaminhada exclusivamente à Prescrição Punitiva (Lei 9.873/1999)."
            )

        relatorio_decadencia = (
            f"  - **Data da Infração**: {data_infracao.strftime('%d/%m/%Y')}\n"
            f"  - **Faixa Temporal Identificada**: {faixa_temporal}\n"
            f"  - **Regra Aplicada**: {regra_aplicada}\n"
            f"  - **Incidência COVID (Res. 782)**: {incidencia_covid_texto}\n"
            f"  - **Detalhe do Cálculo**: {detalhe_calculo}"
        )

        _logger.info(
            "[JARIMATH] decadencia=%s | filtro=%s | regra=%s",
            decadencia_encontrada, faixa_temporal, regra_aplicada,
        )
        return (decadencia_encontrada, relatorio_decadencia)

    @staticmethod
    def check_tempestividade(data_protocolo, prazo_final):
        """
        Tempestividade (CTB ART. 285).
        Retorna True se tempestivo (protocolo <= prazo), False se intempestivo, None se dados faltam.
        """
        if not data_protocolo or not prazo_final:
            return None

        data_protocolo = JariMath._parse_date(data_protocolo)
        prazo_final = JariMath._parse_date(prazo_final)

        return data_protocolo <= prazo_final
