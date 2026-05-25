"""
Teste E2E completo — simula julgamento de ponta-a-ponta.

Usa o Consolidado_26015_2022.pdf da pasta Consolidado_teste/ como fixture real,
mockando APENAS as chamadas externas (Anthropic, Gemini, Vertex, Perplexity)
para que o fluxo rode offline sem API keys.

Testa:
- Rota D (mérito disponível — tempestivo, sem prescrição, sem decadência)
- Rota A (intempestivo — pula teses, vai direto pro parecer)
- Rota B (prescrição punitiva — pula teses)
- Transições de fase completas (state machine)
- Criação de models em cada passo
- Cálculos determinísticos (JariMath)
- API response format (contrato frontend)
"""

import datetime
import json
import os
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings

from pareceres.estado import FaseProcesso
from pareceres.models import (
    Admissibilidade, AnaliseTese, Documento, Parecer, Processo,
)
from pareceres.services import ServiceResult

# ─── Fixtures realistas baseadas no Consolidado_26015_2022.pdf ─────────────


# Resposta simulada do Anthropic/Gemini para extração de nome (Fase 2 — versão enxuta)
MOCK_EXTRACAO_RESPONSE = """
RECORRENTE: JOAO DA SILVA TESTE
TIPO_PENALIDADE: multa
INFRACAO_DOCUMENTO: Transitar em velocidade superior a maxima permitida em ate 20% - art. 218 I CTB
""".strip()

# Resposta simulada do Gemini para texto de admissibilidade (Fase 3)
MOCK_ADMISSIBILIDADE_TEXTO = """
## Análise de Admissibilidade

O recurso foi **tempestivo**, protocolado em 05/07/2022, dentro do prazo final de 10/07/2022.

### Prescrição Punitiva
Não configurada. Data da infração: 15/03/2022. Aniversário quinquenal: 15/03/2027.

### Prescrição Intercorrente
Não configurada. Prazo trienal não atingido.

### Decadência
Não configurada. Infração posterior a 22/10/2021 — aplica-se Filtro 3 (Res. 381/2022).

**Rota: D — Mérito disponível para análise de teses.**
""".strip()

# Resposta simulada do Gemini para extração de teses (Fase 4)
MOCK_TESES_EXTRACAO = """
TESE_1: Nulidade da autuação por ausência de identificação do agente autuador
TESE_2: Irregularidade na aferição do equipamento medidor de velocidade
TESE_3: Cerceamento de defesa por ausência de notificação regular
""".strip()

# Resposta simulada do Vertex/Perplexity + Gemini para análise de teses (Fase 4b)
MOCK_TESES_ANALISE = """
## Análise Cruzada das Teses

**Tese 1 — Nulidade por ausência de identificação do agente:**
Fundamentação frágil. O art. 280 §4º do CTB dispensa identificação em equipamentos eletrônicos.
Referência: Súmula 312 STJ.

**Tese 2 — Irregularidade do equipamento:**
Verificação do INMETRO dentro do prazo. Certificado válido. Tese improcedente.

**Tese 3 — Cerceamento de defesa:**
AR assinado em 10/06/2022 comprova ciência. Notificação regular.
""".strip()

# Resposta simulada do Gemini para parecer final (Fase 5)
MOCK_PARECER_TEXTO = """
# PARECER TÉCNICO

**Processo Administrativo:** PA 26015/2022
**SGPE:** SGPE-001
**Recorrente:** JOAO DA SILVA TESTE
**Relator:** Dr. Julgador

## I - RELATÓRIO

Trata-se de recurso interposto por JOAO DA SILVA TESTE contra auto de infração
lavrado em 15/03/2022, por infração ao art. 218, I, do CTB.

## II - ADMISSIBILIDADE

O recurso é tempestivo, protocolado dentro do prazo legal.
Não há prescrição punitiva, intercorrente ou decadência.

## III - MÉRITO

### Tese 1: Nulidade da autuação
NÃO ACOLHIDA. O art. 280 §4º CTB dispensa identificação do agente em
equipamentos eletrônicos.

### Tese 2: Irregularidade do equipamento
NÃO ACOLHIDA. Certificado INMETRO válido à época da infração.

### Tese 3: Cerceamento de defesa
NÃO ACOLHIDA. AR comprova ciência regular da penalidade.

## IV - CONCLUSÃO

Ante o exposto, OPINO pelo **INDEFERIMENTO** do recurso.

RESULTADO: INDEFERIDO
""".strip()


# ─── Helpers ───────────────────────────────────────────────────────────────


def _mock_gemini_client(text_response):
    """Cria um mock do GeminiClient que retorna text_response no generate()."""
    mock_response = MagicMock()
    mock_response.text = text_response

    mock_client = MagicMock()
    mock_client.generate.return_value = (mock_response, "gemini-2.5-flash")
    mock_client.upload_file.return_value = MagicMock()
    mock_client.log_usage.return_value = None
    return mock_client


def _get_pdf_fixture():
    """Retorna o PDF real de teste se existir, senão cria um fake."""
    pdf_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "Consolidado_teste", "Consolidado_26015_2022.pdf"
    )
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            return SimpleUploadedFile("Consolidado_26015_2022.pdf", f.read(), content_type="application/pdf")
    # Fallback: PDF mínimo válido
    return SimpleUploadedFile(
        "Consolidado_26015_2022.pdf",
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF",
        content_type="application/pdf",
    )


# ─── Teste E2E: Rota D (mérito disponível) ─────────────────────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class E2ERotaDTest(TestCase):
    """Fluxo completo: upload → extração → admissibilidade → teses → parecer → auditoria."""

    def setUp(self):
        self.user = User.objects.create_user(username="julgador_e2e", password="test123")
        self.client = Client()
        self.client.login(username="julgador_e2e", password="test123")

    def _json_post(self, url, data=None):
        return self.client.post(
            url,
            data=json.dumps(data or {}),
            content_type="application/json",
        )

    def _json_get(self, url):
        resp = self.client.get(url)
        return resp

    # ── Passo 1: Criar processo e upload do PDF ──

    def test_fluxo_completo_rota_d(self):
        """Teste ponta-a-ponta: Rota D (mérito disponível) → INDEFERIDO."""

        # ─ Criar processo ─
        resp = self.client.get("/processo/novo/")
        self.assertEqual(resp.status_code, 302)
        processo = Processo.objects.filter(user=self.user).order_by("-pk").first()
        self.assertIsNotNone(processo)
        self.assertEqual(processo.fase, FaseProcesso.DOCUMENTOS)
        pk = processo.pk

        # ─ Verificar wizard renderiza ─
        resp = self.client.get(f"/processo/{pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "wizard-root")

        # ─ Verificar API fase ─
        resp = self._json_get(f"/api/processo/{pk}/fase/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["fase"], FaseProcesso.DOCUMENTOS)
        self.assertEqual(data["passo"], 1)

        # ─ Passo 1: Upload PDF ─
        pdf_file = _get_pdf_fixture()
        with patch("pareceres.services.service_documentos._tentar_anthropic", return_value=MOCK_EXTRACAO_RESPONSE), \
             patch("pareceres.services.service_documentos._tentar_gemini", return_value=None):
            resp = self.client.post(
                f"/api/processo/{pk}/documentos/",
                {"consolidado": pdf_file},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        # Verificar documento criado
        self.assertTrue(Documento.objects.filter(processo=processo, tipo="consolidado").exists())

        # Verificar fase avançou para DOCUMENTOS_EXTRAIDOS
        processo.refresh_from_db()
        self.assertEqual(processo.fase, FaseProcesso.DOCUMENTOS_EXTRAIDOS)

        # Verificar nome extraído (versão enxuta: só nome)
        self.assertEqual(processo.recorrente, "JOAO DA SILVA TESTE")

        # Verificar Admissibilidade criada com tipo_penalidade
        adm = processo.admissibilidade
        self.assertIsNotNone(adm)
        self.assertEqual(adm.tipo_penalidade, "multa")

        # ─ Passo 2: Dados extraídos (leitura) ─
        resp = self._json_get(f"/api/processo/{pk}/dados-extraidos/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["recorrente"], "JOAO DA SILVA TESTE")

        # ─ Passo 2: Confirmar dados (MJ preenche datas) + disparar admissibilidade ─
        with patch("pareceres.integrations.gemini.GeminiClient",
                    return_value=_mock_gemini_client(MOCK_ADMISSIBILIDADE_TEXTO)):
            resp = self._json_post(f"/api/processo/{pk}/confirmar-dados/", {
                "data_sessao": "2025-06-15",
                "recorrente": "JOAO DA SILVA TESTE",
                "prazo_final": "2022-07-10",
                "data_protocolo": "2022-07-05",
                "paginas_defesa": "15-22",
                "data_infracao": "2022-03-15",
                "data_na": "2022-03-25",
                "data_np": "2022-06-10",
                "tipo_penalidade": "multa",
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        # Verificar fase
        processo.refresh_from_db()
        self.assertIn(processo.fase, [
            FaseProcesso.ADMISSIBILIDADE,
            FaseProcesso.ADMISSIBILIDADE_AGUARDANDO,
        ])

        # Verificar data_sessao e datas preenchidas
        self.assertEqual(processo.data_sessao, datetime.date(2025, 6, 15))
        self.assertEqual(processo.prazo_final, datetime.date(2022, 7, 10))
        self.assertEqual(processo.data_protocolo, datetime.date(2022, 7, 5))

        adm.refresh_from_db()
        self.assertEqual(adm.data_infracao, datetime.date(2022, 3, 15))
        self.assertEqual(adm.data_na, datetime.date(2022, 3, 25))
        self.assertEqual(adm.data_np, datetime.date(2022, 6, 10))

        # Se fase ficou em ADMISSIBILIDADE (task síncrona falhou),
        # executar service diretamente
        if processo.fase == FaseProcesso.ADMISSIBILIDADE:
            with patch("pareceres.integrations.gemini.GeminiClient",
                        return_value=_mock_gemini_client(MOCK_ADMISSIBILIDADE_TEXTO)):
                from pareceres.services.service_admissibilidade import execute
                result = execute(processo)
            self.assertTrue(result.ok, f"Admissibilidade falhou: {result.erro}")
            processo.refresh_from_db()

        # ─ Passo 3: Admissibilidade (leitura) ─
        resp = self._json_get(f"/api/processo/{pk}/admissibilidade/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Verificar flags computadas por JariMath
        flags = data["flags"]
        self.assertTrue(flags["is_tempestivo"], "Protocolo 05/07 dentro do prazo 10/07 → tempestivo")
        self.assertFalse(flags["has_prescricao_punitiva"], "Infração 15/03/2022, sessão 15/06/2025 → < 5 anos")
        self.assertFalse(flags["has_prescricao_intercorrente"], "Protocolo 05/07/2022, sessão 15/06/2025 → < 3 anos")

        self.assertTrue(data["aguardando"])  # Deve estar ADMISSIBILIDADE_AGUARDANDO

        # ─ Passo 3: Confirmar admissibilidade (julgador concorda) ─
        with patch("pareceres.services.service_teses.execute_extracao") as mock_teses_ext, \
             patch("pareceres.services.service_teses.execute_analise") as mock_teses_ana:
            # Mockar extração de teses — criar 3 teses
            def side_effect_extracao(proc):
                AnaliseTese.objects.create(processo=proc, ordem=1,
                    titulo="Nulidade da autuação por ausência de identificação do agente autuador")
                AnaliseTese.objects.create(processo=proc, ordem=2,
                    titulo="Irregularidade na aferição do equipamento medidor de velocidade")
                AnaliseTese.objects.create(processo=proc, ordem=3,
                    titulo="Cerceamento de defesa por ausência de notificação regular")
                proc.avancar_fase(FaseProcesso.TESE_AGUARDANDO)
                return ServiceResult.sucesso("3 teses extraídas", teses_count=3)

            mock_teses_ext.side_effect = side_effect_extracao
            mock_teses_ana.return_value = ServiceResult.sucesso("Teses analisadas")

            resp = self._json_post(f"/api/processo/{pk}/admissibilidade/confirmar/", {
                "julgador_tempestivo": "true",
                "julgador_punitiva": "false",
                "julgador_intercorrente": "false",
                "julgador_intercorrente_bienal": "false",
                "julgador_decadencia": "false",
            })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["rota"], "D")
        self.assertFalse(data["skip_teses"])

        # Verificar julgador_* salvos
        adm.refresh_from_db()
        self.assertTrue(adm.julgador_tempestivo)
        self.assertFalse(adm.julgador_prescricao_punitiva)
        self.assertEqual(adm.rota, "D")

        # Verificar teses criadas
        processo.refresh_from_db()
        self.assertEqual(processo.teses.count(), 3)

        # ─ Passo 4: Teses (leitura) ─
        resp = self._json_get(f"/api/processo/{pk}/teses/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["teses"]), 3)
        self.assertEqual(data["teses"][0]["ordem"], 1)
        self.assertIn("Nulidade", data["teses"][0]["titulo"])

        # ─ Passo 4: Confirmar teses (julgador rejeita todas) ─
        tese_ids = [t["id"] for t in data["teses"]]
        decisoes = {str(tid): "false" for tid in tese_ids}

        with patch("pareceres.services.service_parecer.execute") as mock_parecer:
            def side_effect_parecer(proc):
                parecer, _ = Parecer.objects.get_or_create(processo=proc)
                parecer.texto_ia = MOCK_PARECER_TEXTO
                parecer.provider = "gemini"
                parecer.save()
                proc.resultado_final = "INDEFERIDO"
                proc.save(update_fields=["resultado_final"])
                proc.fase = FaseProcesso.AUDITORIA
                proc.save(update_fields=["fase"])
                return ServiceResult.sucesso("Parecer gerado")

            mock_parecer.side_effect = side_effect_parecer

            resp = self._json_post(f"/api/processo/{pk}/teses/confirmar/", {
                "decisoes": decisoes,
            })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        # Verificar teses marcadas como não acolhidas
        for tese in processo.teses.all():
            self.assertFalse(tese.acolhida)

        # ─ Passo 5: Parecer (leitura) ─
        processo.refresh_from_db()
        resp = self._json_get(f"/api/processo/{pk}/parecer/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("PARECER TÉCNICO", data["texto"])
        self.assertEqual(data["resultado"], "INDEFERIDO")
        self.assertEqual(data["provider"], "gemini")

        # ─ Passo 5: Editar parecer ─
        texto_editado = "<p>Texto editado pelo julgador com ajustes manuais.</p>"
        resp = self._json_post(f"/api/processo/{pk}/parecer/editar/", {
            "texto_editado": texto_editado,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["conteudo_final"], texto_editado)

        # ─ Passo 6: Auditoria e finalização ─
        with patch("pareceres.services.service_auditoria.execute") as mock_audit:
            def side_effect_audit(proc):
                parecer = proc.parecer
                parecer.blindagem_score = 85
                parecer.blindagem_detalhes = "## Auditoria\nAprovado com 85/100."
                parecer.checklist_auditoria = [
                    {"label": "Cabeçalho correto", "ok": True},
                    {"label": "Resultado coerente", "ok": True},
                    {"label": "Admissibilidade coerente", "ok": True},
                    {"label": "Prescrição punitiva", "ok": True},
                    {"label": "Prescrição intercorrente", "ok": True},
                    {"label": "Decadência temporal", "ok": True},
                    {"label": "Teses analisadas", "ok": True},
                    {"label": "Teses prejudicadas", "ok": True},
                    {"label": "Citações normativas", "ok": True},
                    {"label": "Sem menções IA", "ok": False},
                ]
                parecer.save()
                proc.avancar_fase(FaseProcesso.FINALIZADO)
                return ServiceResult.sucesso("Auditoria concluída", score=85)

            mock_audit.side_effect = side_effect_audit

            resp = self._json_post(f"/api/processo/{pk}/auditoria/finalizar/")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        # Verificar estado final
        processo.refresh_from_db()
        self.assertEqual(processo.fase, FaseProcesso.FINALIZADO)
        self.assertEqual(processo.resultado_final, "INDEFERIDO")

        # Verificar parecer final
        parecer = processo.parecer
        self.assertEqual(parecer.blindagem_score, 85)
        self.assertEqual(len(parecer.checklist_auditoria), 10)
        self.assertEqual(parecer.conteudo_final, texto_editado)

        # ─ Verificar que API retorna dados do passo 6 corretamente ─
        resp = self._json_get(f"/api/processo/{pk}/parecer/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["blindagem_score"], 85)
        self.assertIsNotNone(data["checklist"])
        self.assertEqual(len(data["checklist"]), 10)


# ─── Teste E2E: Rota A (intempestivo → mérito prejudicado) ────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class E2ERotaATest(TestCase):
    """Fluxo encurtado: Rota A pula teses e vai direto pro parecer."""

    def setUp(self):
        self.user = User.objects.create_user(username="julgador_rota_a", password="test123")
        self.client = Client()
        self.client.login(username="julgador_rota_a", password="test123")

    def _json_post(self, url, data=None):
        return self.client.post(
            url, data=json.dumps(data or {}), content_type="application/json",
        )

    def test_fluxo_rota_a_intempestivo(self):
        """Rota A: intempestivo → pula teses → parecer direto → INDEFERIDO."""

        # Criar processo
        resp = self.client.get("/processo/novo/")
        processo = Processo.objects.filter(user=self.user).order_by("-pk").first()
        pk = processo.pk

        # Upload
        pdf_file = _get_pdf_fixture()
        with patch("pareceres.services.service_documentos._tentar_anthropic",
                    return_value=MOCK_EXTRACAO_RESPONSE):
            resp = self.client.post(
                f"/api/processo/{pk}/documentos/",
                {"consolidado": pdf_file},
            )
        self.assertEqual(resp.status_code, 200)

        processo.refresh_from_db()
        self.assertEqual(processo.fase, FaseProcesso.DOCUMENTOS_EXTRAIDOS)

        # Confirmar dados — MJ preenche datas que geram intempestividade
        # Protocolo 15/07 > prazo 01/06 → intempestivo
        with patch("pareceres.integrations.gemini.GeminiClient",
                    return_value=_mock_gemini_client("Recurso **intempestivo**. Rota A.")):
            resp = self._json_post(f"/api/processo/{pk}/confirmar-dados/", {
                "data_sessao": "2025-06-15",
                "recorrente": "JOAO DA SILVA TESTE",
                "prazo_final": "2022-06-01",
                "data_protocolo": "2022-07-15",
                "paginas_defesa": "10-20",
                "data_infracao": "2022-03-15",
            })
        self.assertEqual(resp.status_code, 200)

        # Garantir que admissibilidade foi calculada
        processo.refresh_from_db()
        if processo.fase == FaseProcesso.ADMISSIBILIDADE:
            with patch("pareceres.integrations.gemini.GeminiClient",
                        return_value=_mock_gemini_client("Recurso **intempestivo**. Rota A.")):
                from pareceres.services.service_admissibilidade import execute
                execute(processo)
            processo.refresh_from_db()

        self.assertEqual(processo.fase, FaseProcesso.ADMISSIBILIDADE_AGUARDANDO)

        # Verificar intempestividade
        adm = processo.admissibilidade
        self.assertFalse(adm.is_tempestivo, "Protocolo 15/07 > prazo 01/06 → intempestivo")

        # Confirmar admissibilidade — julgador concorda: intempestivo
        with patch("pareceres.services.service_parecer.execute") as mock_parecer:
            def side_effect_parecer(proc):
                parecer, _ = Parecer.objects.get_or_create(processo=proc)
                parecer.texto_ia = "PARECER: Recurso intempestivo. INDEFERIDO."
                parecer.provider = "gemini"
                parecer.save()
                proc.resultado_final = "INDEFERIDO"
                proc.save(update_fields=["resultado_final"])
                proc.fase = FaseProcesso.AUDITORIA
                proc.save(update_fields=["fase"])
                return ServiceResult.sucesso("Parecer gerado (Rota A)")

            mock_parecer.side_effect = side_effect_parecer

            resp = self._json_post(f"/api/processo/{pk}/admissibilidade/confirmar/", {
                "julgador_tempestivo": "false",
                "julgador_punitiva": "false",
                "julgador_intercorrente": "false",
                "julgador_intercorrente_bienal": "false",
                "julgador_decadencia": "false",
            })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["rota"], "A")
        self.assertTrue(data["skip_teses"], "Rota A deve pular teses")

        # Verificar que pulou para PARECER (sem teses)
        processo.refresh_from_db()
        self.assertEqual(processo.teses.count(), 0)
        self.assertIsNotNone(processo.parecer)

        # Auditoria
        with patch("pareceres.services.service_auditoria.execute") as mock_audit:
            def side_effect_audit(proc):
                parecer = proc.parecer
                parecer.blindagem_score = 90
                parecer.blindagem_detalhes = "OK"
                parecer.checklist_auditoria = {"cabecalho": True, "resultado": True}
                parecer.save()
                proc.avancar_fase(FaseProcesso.FINALIZADO)
                return ServiceResult.sucesso("OK", score=90)

            mock_audit.side_effect = side_effect_audit
            resp = self._json_post(f"/api/processo/{pk}/auditoria/finalizar/")

        self.assertEqual(resp.status_code, 200)
        processo.refresh_from_db()
        self.assertEqual(processo.fase, FaseProcesso.FINALIZADO)
        self.assertEqual(processo.resultado_final, "INDEFERIDO")


# ─── Teste E2E: Rota B (prescrição punitiva) ──────────────────────────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class E2ERotaBTest(TestCase):
    """Rota B: prescrição punitiva → pula teses → parecer direto → DEFERIDO."""

    def setUp(self):
        self.user = User.objects.create_user(username="julgador_rota_b", password="test123")
        self.client = Client()
        self.client.login(username="julgador_rota_b", password="test123")

    def _json_post(self, url, data=None):
        return self.client.post(
            url, data=json.dumps(data or {}), content_type="application/json",
        )

    def test_fluxo_rota_b_prescricao(self):
        """Rota B: prescrição punitiva → pula teses → DEFERIDO."""

        # Criar processo
        resp = self.client.get("/processo/novo/")
        processo = Processo.objects.filter(user=self.user).order_by("-pk").first()
        pk = processo.pk

        # Upload
        pdf_file = _get_pdf_fixture()
        with patch("pareceres.services.service_documentos._tentar_anthropic",
                    return_value=MOCK_EXTRACAO_RESPONSE):
            resp = self.client.post(
                f"/api/processo/{pk}/documentos/",
                {"consolidado": pdf_file},
            )
        self.assertEqual(resp.status_code, 200)

        # Confirmar dados — infração antiga (mais de 5 anos) → prescrição punitiva
        processo.refresh_from_db()
        with patch("pareceres.integrations.gemini.GeminiClient",
                    return_value=_mock_gemini_client("Prescrição punitiva configurada. Rota B.")):
            resp = self._json_post(f"/api/processo/{pk}/confirmar-dados/", {
                "data_sessao": "2025-06-15",
                "recorrente": "JOAO DA SILVA TESTE",
                "prazo_final": "2019-04-15",
                "data_protocolo": "2019-04-10",
                "paginas_defesa": "10-20",
                "data_infracao": "2019-01-10",
                "data_na": "2019-01-20",
                "data_np": "2019-03-15",
                "tipo_penalidade": "multa",
            })

        processo.refresh_from_db()
        if processo.fase == FaseProcesso.ADMISSIBILIDADE:
            with patch("pareceres.integrations.gemini.GeminiClient",
                        return_value=_mock_gemini_client("Prescrição punitiva. Rota B.")):
                from pareceres.services.service_admissibilidade import execute
                execute(processo)
            processo.refresh_from_db()

        # Verificar prescrição punitiva
        adm = processo.admissibilidade
        self.assertTrue(
            adm.has_prescricao_punitiva,
            f"Infração 10/01/2019, sessão 15/06/2025 → >5 anos → prescrito. "
            f"data_infracao={adm.data_infracao}"
        )

        # Confirmar admissibilidade — julgador marca prescrição
        with patch("pareceres.services.service_parecer.execute") as mock_parecer:
            def side_effect_parecer(proc):
                parecer, _ = Parecer.objects.get_or_create(processo=proc)
                parecer.texto_ia = "PARECER: Prescrição punitiva configurada. DEFERIDO."
                parecer.provider = "gemini"
                parecer.save()
                proc.resultado_final = "DEFERIDO"
                proc.save(update_fields=["resultado_final"])
                proc.fase = FaseProcesso.AUDITORIA
                proc.save(update_fields=["fase"])
                return ServiceResult.sucesso("Parecer Rota B")

            mock_parecer.side_effect = side_effect_parecer

            resp = self._json_post(f"/api/processo/{pk}/admissibilidade/confirmar/", {
                "julgador_tempestivo": "true",
                "julgador_punitiva": "true",
                "julgador_intercorrente": "false",
                "julgador_intercorrente_bienal": "false",
                "julgador_decadencia": "false",
            })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["rota"], "B")
        self.assertTrue(data["skip_teses"])

        processo.refresh_from_db()
        self.assertEqual(processo.resultado_final, "DEFERIDO")


# ─── Teste de contrato da API (formato JSON esperado pelo frontend) ────────


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class APIContractTest(TestCase):
    """Valida que cada endpoint retorna o formato exato que o wizard.js espera."""

    def setUp(self):
        self.user = User.objects.create_user(username="contract_test", password="test123")
        self.client = Client()
        self.client.login(username="contract_test", password="test123")
        self.processo = Processo.objects.create(user=self.user, pa="PA-CONTRACT")

    def test_api_fase_contract(self):
        """GET /api/processo/<pk>/fase/ → {fase, passo, label}"""
        resp = self.client.get(f"/api/processo/{self.processo.pk}/fase/")
        data = resp.json()
        self.assertIn("fase", data)
        self.assertIn("passo", data)
        self.assertIn("label", data)
        self.assertIsInstance(data["passo"], int)

    def test_api_dados_extraidos_contract(self):
        """GET /api/processo/<pk>/dados-extraidos/ → {recorrente, data_infracao, ...}"""
        Admissibilidade.objects.create(
            processo=self.processo,
            data_infracao=datetime.date(2022, 3, 15),
        )
        resp = self.client.get(f"/api/processo/{self.processo.pk}/dados-extraidos/")
        data = resp.json()
        for field in ("recorrente", "prazo_final", "data_protocolo",
                      "paginas_defesa", "data_sessao", "data_infracao",
                      "data_na", "data_np", "data_instauracao", "tipo_penalidade"):
            self.assertIn(field, data, f"Campo '{field}' ausente no contrato")

    def test_api_admissibilidade_contract(self):
        """GET /api/processo/<pk>/admissibilidade/ → {texto, flags, julgador, aguardando}"""
        self.processo.fase = FaseProcesso.ADMISSIBILIDADE_AGUARDANDO
        self.processo.save(update_fields=["fase"])
        Admissibilidade.objects.create(
            processo=self.processo,
            is_tempestivo=True,
            has_prescricao_punitiva=False,
            texto_resultado="Texto teste",
        )
        resp = self.client.get(f"/api/processo/{self.processo.pk}/admissibilidade/")
        data = resp.json()
        self.assertIn("flags", data)
        self.assertIn("julgador", data)
        self.assertIn("texto", data)
        self.assertIn("aguardando", data)

        flags = data["flags"]
        for flag in ("is_tempestivo", "has_prescricao_punitiva",
                     "has_prescricao_intercorrente", "has_prescricao_intercorrente_bienal",
                     "has_decadencia"):
            self.assertIn(flag, flags, f"Flag '{flag}' ausente")

        julgador = data["julgador"]
        for key in ("tempestivo", "punitiva", "intercorrente",
                    "intercorrente_bienal", "decadencia"):
            self.assertIn(key, julgador, f"Julgador key '{key}' ausente")

    def test_api_teses_contract(self):
        """GET /api/processo/<pk>/teses/ → {teses: [{id, ordem, titulo, fundamentacao, acolhida}]}"""
        AnaliseTese.objects.create(
            processo=self.processo, ordem=1,
            titulo="Tese de teste",
            fundamentacao="Fund. teste",
        )
        resp = self.client.get(f"/api/processo/{self.processo.pk}/teses/")
        data = resp.json()
        self.assertIn("teses", data)
        self.assertEqual(len(data["teses"]), 1)
        tese = data["teses"][0]
        for field in ("id", "ordem", "titulo", "fundamentacao", "acolhida"):
            self.assertIn(field, tese, f"Campo '{field}' ausente na tese")

    def test_api_parecer_contract(self):
        """GET /api/processo/<pk>/parecer/ → {texto, texto_ia, provider, resultado, ...}"""
        Parecer.objects.create(
            processo=self.processo,
            texto_ia="Texto IA",
            provider="gemini",
            blindagem_score=90,
            checklist_auditoria=[{"label": "item1", "ok": True}],
        )
        self.processo.resultado_final = "INDEFERIDO"
        self.processo.save(update_fields=["resultado_final"])

        resp = self.client.get(f"/api/processo/{self.processo.pk}/parecer/")
        data = resp.json()
        for field in ("texto", "texto_ia", "texto_editado", "provider",
                      "resultado", "blindagem_score", "blindagem_detalhes", "checklist"):
            self.assertIn(field, data, f"Campo '{field}' ausente no parecer")
        self.assertEqual(data["resultado"], "INDEFERIDO")
        self.assertEqual(data["blindagem_score"], 90)

    def test_api_forum_contract(self):
        """GET /api/forum/ → {posts: [...]}"""
        resp = self.client.get("/api/forum/")
        data = resp.json()
        self.assertIn("posts", data)
        self.assertIsInstance(data["posts"], list)

    def test_api_teses_banco_contract(self):
        """GET /api/teses-banco/ → {teses: [...]}"""
        resp = self.client.get("/api/teses-banco/")
        data = resp.json()
        self.assertIn("teses", data)
        self.assertIsInstance(data["teses"], list)

    def test_api_pastas_contract(self):
        """GET /api/pastas/ → {pastas: [...]}"""
        resp = self.client.get("/api/pastas/")
        data = resp.json()
        self.assertIn("pastas", data)
        self.assertIsInstance(data["pastas"], list)


# ─── Teste de transições de estado inválidas ──────────────────────────────


class TransicaoEstadoTest(TestCase):
    """Verifica que transições inválidas são rejeitadas."""

    def setUp(self):
        self.user = User.objects.create_user(username="estado_test", password="test123")
        self.client = Client()
        self.client.login(username="estado_test", password="test123")

    def test_upload_em_fase_errada(self):
        processo = Processo.objects.create(
            user=self.user, fase=FaseProcesso.ADMISSIBILIDADE_AGUARDANDO
        )
        pdf_file = SimpleUploadedFile("test.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
        resp = self.client.post(
            f"/api/processo/{processo.pk}/documentos/",
            {"consolidado": pdf_file},
        )
        self.assertEqual(resp.status_code, 400)

    def test_confirmar_dados_em_fase_errada(self):
        processo = Processo.objects.create(user=self.user, fase=FaseProcesso.DOCUMENTOS)
        resp = self.client.post(
            f"/api/processo/{processo.pk}/confirmar-dados/",
            data=json.dumps({"data_sessao": "2025-01-01"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_confirmar_admissibilidade_em_fase_errada(self):
        processo = Processo.objects.create(user=self.user, fase=FaseProcesso.DOCUMENTOS)
        resp = self.client.post(
            f"/api/processo/{processo.pk}/admissibilidade/confirmar/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_confirmar_teses_em_fase_errada(self):
        processo = Processo.objects.create(user=self.user, fase=FaseProcesso.DOCUMENTOS)
        resp = self.client.post(
            f"/api/processo/{processo.pk}/teses/confirmar/",
            data=json.dumps({"decisoes": {}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_finalizar_em_fase_errada(self):
        processo = Processo.objects.create(user=self.user, fase=FaseProcesso.DOCUMENTOS)
        resp = self.client.post(
            f"/api/processo/{processo.pk}/auditoria/finalizar/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


# ─── Teste JariMath integrado com Admissibilidade ─────────────────────────


class JariMathIntegracaoTest(TestCase):
    """Verifica que os cálculos JariMath são corretos para cenários reais."""

    def test_tempestivo_dentro_do_prazo(self):
        from pareceres.math import JariMath
        result = JariMath.check_tempestividade(
            data_protocolo=datetime.date(2022, 7, 5),
            prazo_final=datetime.date(2022, 7, 10),
        )
        self.assertTrue(result)

    def test_intempestivo_fora_do_prazo(self):
        from pareceres.math import JariMath
        result = JariMath.check_tempestividade(
            data_protocolo=datetime.date(2022, 7, 15),
            prazo_final=datetime.date(2022, 7, 10),
        )
        self.assertFalse(result)

    def test_prescricao_punitiva_5_anos(self):
        from pareceres.math import JariMath
        result = JariMath.check_prescription_punitiva(
            data_infracao=datetime.date(2019, 1, 10),
            data_sessao=datetime.date(2025, 6, 15),
        )
        self.assertTrue(result, "Infração 10/01/2019, sessão 15/06/2025 → >5 anos")

    def test_sem_prescricao_punitiva(self):
        from pareceres.math import JariMath
        result, _ = JariMath.check_prescription_punitiva(
            data_infracao=datetime.date(2022, 3, 15),
            data_sessao=datetime.date(2025, 6, 15),
        )
        self.assertFalse(result, "Infração 15/03/2022, sessão 15/06/2025 → <5 anos")

    def test_prescricao_intercorrente_3_anos(self):
        from pareceres.math import JariMath
        prescrito, _ = JariMath.check_prescription_intercorrente(
            data_protocolo=datetime.date(2021, 1, 1),
            data_sessao=datetime.date(2025, 6, 15),
        )
        self.assertTrue(prescrito, "Protocolo 01/01/2021, sessão 15/06/2025 → >3 anos")

    def test_sem_prescricao_intercorrente(self):
        from pareceres.math import JariMath
        prescrito, _ = JariMath.check_prescription_intercorrente(
            data_protocolo=datetime.date(2022, 7, 5),
            data_sessao=datetime.date(2025, 6, 15),
        )
        self.assertFalse(prescrito, "Protocolo 05/07/2022, sessão 15/06/2025 → <3 anos")

    def test_rota_property_a(self):
        """Admissibilidade.rota retorna 'A' quando intempestivo."""
        user = User.objects.create_user(username="math_test", password="t")
        proc = Processo.objects.create(user=user)
        adm = Admissibilidade.objects.create(
            processo=proc,
            is_tempestivo=False,
            has_prescricao_punitiva=False,
            has_prescricao_intercorrente=False,
            has_prescricao_intercorrente_bienal=False,
            has_decadencia=False,
        )
        self.assertEqual(adm.rota, "A")

    def test_rota_property_b(self):
        """Admissibilidade.rota retorna 'B' quando prescrição punitiva."""
        user = User.objects.create_user(username="math_b", password="t")
        proc = Processo.objects.create(user=user)
        adm = Admissibilidade.objects.create(
            processo=proc,
            is_tempestivo=True,
            has_prescricao_punitiva=True,
        )
        self.assertEqual(adm.rota, "B")

    def test_rota_property_c(self):
        """Admissibilidade.rota retorna 'C' quando decadência."""
        user = User.objects.create_user(username="math_c", password="t")
        proc = Processo.objects.create(user=user)
        adm = Admissibilidade.objects.create(
            processo=proc,
            is_tempestivo=True,
            has_decadencia=True,
        )
        self.assertEqual(adm.rota, "C")

    def test_rota_property_d(self):
        """Admissibilidade.rota retorna 'D' quando tudo limpo."""
        user = User.objects.create_user(username="math_d", password="t")
        proc = Processo.objects.create(user=user)
        adm = Admissibilidade.objects.create(
            processo=proc,
            is_tempestivo=True,
            has_prescricao_punitiva=False,
            has_prescricao_intercorrente=False,
            has_prescricao_intercorrente_bienal=False,
            has_decadencia=False,
        )
        self.assertEqual(adm.rota, "D")

    def test_julgador_override_muda_rota(self):
        """Julgador override muda a rota (flag_* prevalece)."""
        user = User.objects.create_user(username="math_override", password="t")
        proc = Processo.objects.create(user=user)
        adm = Admissibilidade.objects.create(
            processo=proc,
            is_tempestivo=True,  # auto: tempestivo
            julgador_tempestivo=False,  # julgador: intempestivo
        )
        self.assertEqual(adm.rota, "A", "Julgador override deve prevalecer")
        self.assertFalse(adm.flag_tempestivo)
