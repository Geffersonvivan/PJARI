"""
Suite de regressão para JariMath — cálculos de prazos jurídicos.
Portado de P-Jari_antigo/chat/tests/test_jari_math.py.
"""

import datetime

from django.test import TestCase

from pareceres.math import JariMath


class JariMathBasicTests(TestCase):

    def test_is_leap_year(self):
        self.assertTrue(JariMath.is_leap_year(2020))
        self.assertTrue(JariMath.is_leap_year(2024))
        self.assertFalse(JariMath.is_leap_year(2021))
        self.assertFalse(JariMath.is_leap_year(2023))

    def test_count_leap_years(self):
        self.assertEqual(JariMath.count_leap_years(2020, 2025), 2)
        self.assertEqual(JariMath.count_leap_years(2022, 2025), 1)

    def test_calculate_days_diff(self):
        self.assertEqual(JariMath.calculate_days_diff("2024-01-01", "2024-01-02"), 1)
        self.assertEqual(JariMath.calculate_days_diff("2021-01-01", "2022-01-01"), 365)
        self.assertEqual(JariMath.calculate_days_diff("2023-03-01", "2024-03-01"), 366)


# ---------------------------------------------------------------------------
# Tempestividade
# ---------------------------------------------------------------------------


class TestTempestividade(TestCase):

    def test_tempestivo_mesmo_dia(self):
        self.assertTrue(JariMath.check_tempestividade("2024-05-10", "2024-05-10"))

    def test_tempestivo_antes_prazo(self):
        self.assertTrue(JariMath.check_tempestividade("2024-05-09", "2024-05-10"))

    def test_intempestivo_apos_prazo(self):
        self.assertFalse(JariMath.check_tempestividade("2024-05-11", "2024-05-10"))

    def test_nenhuma_data_retorna_none(self):
        self.assertIsNone(JariMath.check_tempestividade(None, datetime.date(2024, 1, 1)))
        self.assertIsNone(JariMath.check_tempestividade(datetime.date(2024, 1, 1), None))
        self.assertIsNone(JariMath.check_tempestividade(None, None))

    def test_intempestivo_um_dia_apos_prazo(self):
        prazo = datetime.date(2024, 3, 15)
        protocolo = prazo + datetime.timedelta(days=1)
        self.assertFalse(JariMath.check_tempestividade(protocolo, prazo))

    def test_string_input(self):
        self.assertTrue(JariMath.check_tempestividade("2024-06-01", "2024-06-10"))
        self.assertFalse(JariMath.check_tempestividade("2024-06-11", "2024-06-10"))


# ---------------------------------------------------------------------------
# Prescrição Intercorrente (3 anos)
# ---------------------------------------------------------------------------


class TestPrescricaoIntercorrente(TestCase):

    def test_nao_prescrito_no_aniversario(self):
        protocolo = datetime.date(2020, 1, 10)
        sessao = datetime.date(2023, 1, 10)
        prescrito, msg = JariMath.check_prescription_intercorrente(protocolo, sessao)
        self.assertFalse(prescrito)
        self.assertEqual(msg, "Prescrição intercorrente não configurada.")

    def test_prescrito_um_dia_apos_aniversario(self):
        protocolo = datetime.date(2020, 1, 10)
        sessao = datetime.date(2023, 1, 11)
        prescrito, msg = JariMath.check_prescription_intercorrente(protocolo, sessao)
        self.assertTrue(prescrito)
        self.assertEqual(msg, "Prescrição intercorrente configurada.")

    def test_exato_aniversario_nao_prescrito(self):
        protocolo = datetime.date(2017, 1, 1)
        sessao = datetime.date(2020, 1, 1)
        prescrito, _ = JariMath.check_prescription_intercorrente(protocolo, sessao)
        self.assertFalse(prescrito)

    def test_um_dia_apos_aniversario_prescrito(self):
        protocolo = datetime.date(2017, 1, 1)
        sessao = datetime.date(2020, 1, 2)
        prescrito, _ = JariMath.check_prescription_intercorrente(protocolo, sessao)
        self.assertTrue(prescrito)

    def test_29fev_bissexto_aniversario_28fev(self):
        protocolo = datetime.date(2020, 2, 29)
        sessao_ok = datetime.date(2023, 2, 28)
        prescrito, _ = JariMath.check_prescription_intercorrente(protocolo, sessao_ok)
        self.assertFalse(prescrito)

        sessao_tarde = datetime.date(2023, 3, 1)
        prescrito, _ = JariMath.check_prescription_intercorrente(protocolo, sessao_tarde)
        self.assertTrue(prescrito)

    def test_dados_insuficientes(self):
        prescrito, msg = JariMath.check_prescription_intercorrente(None, datetime.date(2024, 1, 1))
        self.assertFalse(prescrito)
        self.assertIn("Dados insuficientes", msg)

    def test_string_input(self):
        prescrito, _ = JariMath.check_prescription_intercorrente("2017-01-01", "2019-12-31")
        self.assertFalse(prescrito)
        prescrito, _ = JariMath.check_prescription_intercorrente("2017-01-01", "2020-01-02")
        self.assertTrue(prescrito)


# ---------------------------------------------------------------------------
# Prescrição Intercorrente Bienal (2 anos)
# ---------------------------------------------------------------------------


class TestPrescricaoIntercorrenteBienal(TestCase):

    def test_protocolo_anterior_2024_nao_se_aplica(self):
        """Trava de segurança: protocolo < 01/01/2024 → NÃO SE APLICA."""
        protocolo = datetime.date(2023, 12, 31)
        sessao = datetime.date(2026, 1, 1)
        prescrito, msg = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao)
        self.assertFalse(prescrito)
        self.assertIn("NÃO SE APLICA", msg)

    def test_protocolo_2020_nao_se_aplica(self):
        protocolo = datetime.date(2020, 1, 10)
        sessao = datetime.date(2022, 1, 11)
        prescrito, msg = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao)
        self.assertFalse(prescrito)
        self.assertIn("NÃO SE APLICA", msg)

    def test_nao_prescrito_no_aniversario(self):
        protocolo = datetime.date(2024, 1, 10)
        sessao = datetime.date(2026, 1, 10)
        prescrito, msg = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao)
        self.assertFalse(prescrito)
        self.assertEqual(msg, "Prescrição intercorrente bienal não configurada.")

    def test_prescrito_um_dia_apos_aniversario(self):
        protocolo = datetime.date(2024, 1, 10)
        sessao = datetime.date(2026, 1, 11)
        prescrito, msg = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao)
        self.assertTrue(prescrito)
        self.assertEqual(msg, "Prescrição intercorrente bienal configurada.")

    def test_exato_aniversario_nao_prescrito(self):
        protocolo = datetime.date(2024, 6, 15)
        sessao = datetime.date(2026, 6, 15)
        prescrito, _ = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao)
        self.assertFalse(prescrito)

    def test_um_dia_apos_aniversario_prescrito(self):
        protocolo = datetime.date(2024, 6, 15)
        sessao = datetime.date(2026, 6, 16)
        prescrito, _ = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao)
        self.assertTrue(prescrito)

    def test_29fev_bissexto_aniversario_28fev(self):
        protocolo = datetime.date(2024, 2, 29)
        sessao_ok = datetime.date(2026, 2, 28)
        prescrito, _ = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao_ok)
        self.assertFalse(prescrito)

        sessao_tarde = datetime.date(2026, 3, 1)
        prescrito, _ = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao_tarde)
        self.assertTrue(prescrito)

    def test_dados_insuficientes(self):
        prescrito, msg = JariMath.check_prescription_intercorrente_bienal(None, datetime.date(2024, 1, 1))
        self.assertFalse(prescrito)
        self.assertIn("Dados insuficientes", msg)

    def test_string_input(self):
        prescrito, _ = JariMath.check_prescription_intercorrente_bienal("2024-01-01", "2025-12-31")
        self.assertFalse(prescrito)
        prescrito, _ = JariMath.check_prescription_intercorrente_bienal("2024-01-01", "2026-01-02")
        self.assertTrue(prescrito)

    def test_protocolo_exato_01_01_2024_aplica(self):
        """Protocolo em 01/01/2024 deve aplicar a regra (não é anterior)."""
        protocolo = datetime.date(2024, 1, 1)
        sessao = datetime.date(2026, 1, 2)
        prescrito, msg = JariMath.check_prescription_intercorrente_bienal(protocolo, sessao)
        self.assertTrue(prescrito)
        self.assertEqual(msg, "Prescrição intercorrente bienal configurada.")


# ---------------------------------------------------------------------------
# Prescrição Punitiva (5 anos)
# ---------------------------------------------------------------------------


class TestPrescricaoPunitiva(TestCase):

    def test_nao_prescrito_no_aniversario(self):
        infracao = datetime.date(2016, 6, 15)
        sessao = datetime.date(2021, 6, 15)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao)
        self.assertFalse(result)

    def test_prescrito_um_dia_apos_aniversario(self):
        infracao = datetime.date(2016, 6, 15)
        sessao = datetime.date(2021, 6, 16)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao)
        self.assertTrue(result)

    def test_marcos_interruptivos_reinicia_contagem(self):
        infracao = datetime.date(2015, 1, 1)
        marco = datetime.date(2017, 1, 1)
        sessao = datetime.date(2021, 1, 1)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao, [marco])
        self.assertFalse(result)

    def test_prescrito_apos_5_anos_do_ultimo_marco(self):
        infracao = datetime.date(2015, 1, 1)
        marco = datetime.date(2017, 3, 10)
        sessao = datetime.date(2022, 3, 11)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao, [marco])
        self.assertTrue(result)

    def test_desconto_covid_estende_prazo(self):
        infracao = datetime.date(2015, 6, 1)
        sessao = datetime.date(2020, 6, 2)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao)
        self.assertTrue(result)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao, desconto_covid_dias=256)
        self.assertFalse(result)

    def test_29fev_bissexto(self):
        # 29/02/2016 + 5 anos → 01/03/2021 (2021 não é bissexto)
        infracao = datetime.date(2016, 2, 29)
        sessao_no_dia = datetime.date(2021, 3, 1)  # exato aniversário → NÃO prescrito
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao_no_dia)
        self.assertFalse(result)
        sessao_depois = datetime.date(2021, 3, 2)  # dia seguinte → prescrito
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao_depois)
        self.assertTrue(result)

    def test_multiplos_marcos_usa_mais_recente(self):
        infracao = datetime.date(2013, 1, 1)
        marcos = [
            datetime.date(2014, 1, 1),
            datetime.date(2016, 6, 1),
            datetime.date(2015, 3, 1),
        ]
        sessao_ok = datetime.date(2021, 6, 1)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao_ok, marcos)
        self.assertFalse(result)
        sessao_tarde = datetime.date(2021, 6, 2)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao_tarde, marcos)
        self.assertTrue(result)

    def test_5_anos_corridos(self):
        # 01/01/2015 + 5 anos = 01/01/2020. Sessão no aniversário → NÃO prescrito.
        infracao = datetime.date(2015, 1, 1)
        sessao_aniversario = datetime.date(2020, 1, 1)
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao_aniversario)
        self.assertFalse(result)
        sessao_apos = datetime.date(2020, 1, 2)  # dia seguinte → prescrito
        result, _ = JariMath.check_prescription_punitiva(infracao, sessao_apos)
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# Decadência — FILTRO 1
# ---------------------------------------------------------------------------


class TestDecadenciaFiltro1(TestCase):
    """FILTRO 1: infrações até 11/04/2021 — decadência NUNCA se aplica."""

    def test_hard_stop_atraso_extremo(self):
        infracao = datetime.date(2019, 1, 1)
        notificacao = datetime.date(2021, 1, 1)
        decad, relat = JariMath.check_decadencia(infracao, notificacao)
        self.assertFalse(decad)
        self.assertIn("NÃO SE APLICA", relat)

    def test_hard_stop_limite_filtro(self):
        infracao = datetime.date(2021, 4, 11)
        notificacao = datetime.date(2022, 1, 1)
        decad, relat = JariMath.check_decadencia(infracao, notificacao)
        self.assertFalse(decad)
        self.assertIn("NÃO SE APLICA", relat)


# ---------------------------------------------------------------------------
# Decadência — FILTRO 2
# ---------------------------------------------------------------------------


class TestDecadenciaFiltro2(TestCase):
    """FILTRO 2: infrações de 12/04/2021 a 21/10/2021."""

    def test_dentro_180_nao_decadente(self):
        infracao = datetime.date(2021, 5, 1)
        notificacao = datetime.date(2021, 8, 1)
        decad, _ = JariMath.check_decadencia(infracao, notificacao)
        self.assertFalse(decad)

    def test_exatamente_180_nao_decadente(self):
        infracao = datetime.date(2021, 5, 1)
        notificacao = infracao + datetime.timedelta(days=180)
        decad, _ = JariMath.check_decadencia(infracao, notificacao)
        self.assertFalse(decad)

    def test_apos_180_decadente(self):
        infracao = datetime.date(2021, 5, 1)
        notificacao = datetime.date(2021, 11, 1)
        decad, _ = JariMath.check_decadencia(infracao, notificacao)
        self.assertTrue(decad)

    def test_decisao_final_apos_360_decadente(self):
        infracao = datetime.date(2021, 5, 1)
        notificacao = datetime.date(2021, 9, 1)
        decisao_final = datetime.date(2022, 8, 1)
        decad, relat = JariMath.check_decadencia(infracao, notificacao, decisao_final)
        self.assertTrue(decad)
        self.assertIn("360", relat)

    def test_suspensao_nao_se_aplica(self):
        infracao = datetime.date(2021, 6, 1)
        notificacao = datetime.date(2022, 6, 1)
        decad, relat = JariMath.check_decadencia(infracao, notificacao, tipo_penalidade="suspensao")
        self.assertFalse(decad)
        self.assertIn("NÃO SE APLICA", relat)

    def test_cassacao_nao_se_aplica(self):
        infracao = datetime.date(2021, 8, 1)
        notificacao = datetime.date(2022, 8, 1)
        decad, relat = JariMath.check_decadencia(infracao, notificacao, tipo_penalidade="cassacao")
        self.assertFalse(decad)
        self.assertIn("NÃO SE APLICA", relat)

    def test_sem_flagrante_360_do_conhecimento(self):
        infracao = datetime.date(2021, 5, 1)
        conhecimento = datetime.date(2021, 5, 10)
        notificacao = conhecimento + datetime.timedelta(days=370)
        decad, _ = JariMath.check_decadencia(
            infracao, notificacao, tem_flagrante=False, data_conhecimento_infracao=conhecimento
        )
        self.assertTrue(decad)

    def test_sem_flagrante_dentro_360(self):
        infracao = datetime.date(2021, 5, 1)
        conhecimento = datetime.date(2021, 5, 10)
        notificacao = conhecimento + datetime.timedelta(days=350)
        decad, _ = JariMath.check_decadencia(
            infracao, notificacao, tem_flagrante=False, data_conhecimento_infracao=conhecimento
        )
        self.assertFalse(decad)


# ---------------------------------------------------------------------------
# Decadência — FILTRO 3
# ---------------------------------------------------------------------------


class TestDecadenciaFiltro3(TestCase):
    """FILTRO 3: infrações a partir de 22/10/2021."""

    def test_flagrante_dentro_180(self):
        infracao = datetime.date(2022, 1, 1)
        notificacao = datetime.date(2022, 4, 1)
        decad, _ = JariMath.check_decadencia(infracao, notificacao, tem_flagrante=True)
        self.assertFalse(decad)

    def test_flagrante_exatamente_180(self):
        infracao = datetime.date(2022, 3, 1)
        notificacao = infracao + datetime.timedelta(days=180)
        decad, _ = JariMath.check_decadencia(infracao, notificacao, tem_flagrante=True)
        self.assertFalse(decad)

    def test_flagrante_apos_180_decadente(self):
        infracao = datetime.date(2022, 1, 1)
        notificacao = datetime.date(2022, 8, 1)
        decad, _ = JariMath.check_decadencia(infracao, notificacao, tem_flagrante=True)
        self.assertTrue(decad)

    def test_sem_flagrante_360_do_conhecimento(self):
        infracao = datetime.date(2022, 2, 1)
        conhecimento = datetime.date(2022, 2, 15)
        notificacao = conhecimento + datetime.timedelta(days=370)
        decad, _ = JariMath.check_decadencia(
            infracao, notificacao, tem_flagrante=False, data_conhecimento_infracao=conhecimento
        )
        self.assertTrue(decad)

    def test_sem_flagrante_dentro_360(self):
        infracao = datetime.date(2022, 2, 1)
        conhecimento = datetime.date(2022, 2, 15)
        notificacao = conhecimento + datetime.timedelta(days=350)
        decad, _ = JariMath.check_decadencia(
            infracao, notificacao, tem_flagrante=False, data_conhecimento_infracao=conhecimento
        )
        self.assertFalse(decad)

    def test_suspensao_360_da_conclusao_multa(self):
        infracao = datetime.date(2022, 1, 1)
        conclusao_multa = datetime.date(2022, 6, 1)
        instauracao = conclusao_multa + datetime.timedelta(days=370)
        decad, relat = JariMath.check_decadencia(
            infracao, instauracao, tipo_penalidade="suspensao", data_conclusao_multa=conclusao_multa
        )
        self.assertTrue(decad)
        self.assertIn("360", relat)

    def test_suspensao_dentro_360(self):
        infracao = datetime.date(2022, 1, 1)
        conclusao_multa = datetime.date(2022, 6, 1)
        instauracao = conclusao_multa + datetime.timedelta(days=350)
        decad, _ = JariMath.check_decadencia(
            infracao, instauracao, tipo_penalidade="suspensao", data_conclusao_multa=conclusao_multa
        )
        self.assertFalse(decad)

    def test_primeiro_dia_filtro3(self):
        infracao = datetime.date(2021, 10, 22)
        notificacao = datetime.date(2022, 6, 1)
        decad, relat = JariMath.check_decadencia(infracao, notificacao, tem_flagrante=True)
        self.assertTrue(decad)
        self.assertIn("Após 22/10/2021", relat)
