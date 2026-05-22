"""
Smoke tests para views e endpoints da app pareceres.
Valida fluxo principal do wizard, CRUD de pastas, exclusão de processo,
edição de parecer e feedback.
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase, Client

from pareceres.estado import FaseProcesso
from pareceres.models import (
    Admissibilidade, AnaliseTese, Documento, Parecer, Pasta, Processo,
)


class BaseViewTest(TestCase):
    """Base com user autenticado e processo de teste."""

    def setUp(self):
        self.user = User.objects.create_user(username="julgador", password="test123")
        self.client = Client()
        self.client.login(username="julgador", password="test123")
        self.processo = Processo.objects.create(
            user=self.user,
            pa="PA-001/2025",
            sgpe="SGPE-999",
            recorrente="FULANO DE TAL",
        )


# ── Excluir Processo ─────────────────────────────────────────────────────────


class ExcluirProcessoTest(BaseViewTest):

    def test_excluir_processo(self):
        pk = self.processo.pk
        resp = self.client.post(f"/api/processo/{pk}/excluir/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Processo.objects.filter(pk=pk).exists())

    def test_excluir_processo_outro_user(self):
        outro = User.objects.create_user(username="outro", password="test")
        proc2 = Processo.objects.create(user=outro, pa="PA-002")
        resp = self.client.post(f"/api/processo/{proc2.pk}/excluir/")
        self.assertEqual(resp.status_code, 404)

    def test_excluir_processo_nao_autenticado(self):
        self.client.logout()
        resp = self.client.post(f"/api/processo/{self.processo.pk}/excluir/")
        self.assertEqual(resp.status_code, 302)


# ── Editor de Parecer ────────────────────────────────────────────────────────


class EditarParecerTest(BaseViewTest):

    def setUp(self):
        super().setUp()
        self.processo.fase = FaseProcesso.AUDITORIA
        self.processo.save()
        self.parecer = Parecer.objects.create(
            processo=self.processo,
            texto_ia="Texto gerado pela IA.",
            provider="gemini",
        )

    def test_editar_parecer(self):
        resp = self.client.post(
            f"/api/processo/{self.processo.pk}/parecer/editar/",
            data=json.dumps({"texto_editado": "<p>Texto editado</p>"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.parecer.refresh_from_db()
        self.assertEqual(self.parecer.texto_editado, "<p>Texto editado</p>")
        self.assertEqual(self.parecer.conteudo_final, "<p>Texto editado</p>")

    def test_editar_parecer_vazio(self):
        resp = self.client.post(
            f"/api/processo/{self.processo.pk}/parecer/editar/",
            data=json.dumps({"texto_editado": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_leitura_parecer(self):
        resp = self.client.get(f"/api/processo/{self.processo.pk}/parecer/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["texto_ia"], "Texto gerado pela IA.")
        self.assertEqual(data["provider"], "gemini")


# ── Pastas CRUD ──────────────────────────────────────────────────────────────


class PastasCRUDTest(BaseViewTest):

    def test_criar_pasta(self):
        resp = self.client.post(
            "/api/pastas/criar/",
            data=json.dumps({"nome": "Minha Pasta"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["nome"], "Minha Pasta")
        self.assertTrue(Pasta.objects.filter(nome="Minha Pasta", user=self.user).exists())

    def test_criar_pasta_sem_nome(self):
        resp = self.client.post(
            "/api/pastas/criar/",
            data=json.dumps({"nome": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_listar_pastas(self):
        Pasta.objects.create(user=self.user, nome="P1", posicao=0)
        Pasta.objects.create(user=self.user, nome="P2", posicao=1)
        resp = self.client.get("/api/pastas/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["pastas"]), 2)

    def test_renomear_pasta(self):
        pasta = Pasta.objects.create(user=self.user, nome="Old", posicao=0)
        resp = self.client.post(
            f"/api/pastas/{pasta.id}/renomear/",
            data=json.dumps({"nome": "New"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        pasta.refresh_from_db()
        self.assertEqual(pasta.nome, "New")

    def test_excluir_pasta_nao_exclui_processos(self):
        pasta = Pasta.objects.create(user=self.user, nome="X", posicao=0)
        self.processo.pasta = pasta
        self.processo.save()
        resp = self.client.post(f"/api/pastas/{pasta.id}/excluir/")
        self.assertEqual(resp.status_code, 200)
        self.processo.refresh_from_db()
        self.assertIsNone(self.processo.pasta)

    def test_reordenar_pastas(self):
        p1 = Pasta.objects.create(user=self.user, nome="A", posicao=0)
        p2 = Pasta.objects.create(user=self.user, nome="B", posicao=1)
        resp = self.client.post(
            "/api/pastas/reordenar/",
            data=json.dumps({"ordem": [p2.id, p1.id]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p2.posicao, 0)
        self.assertEqual(p1.posicao, 1)

    def test_mover_processo_para_pasta(self):
        pasta = Pasta.objects.create(user=self.user, nome="Dest", posicao=0)
        resp = self.client.post(
            f"/api/processo/{self.processo.pk}/mover/",
            data=json.dumps({"pasta_id": pasta.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.processo.refresh_from_db()
        self.assertEqual(self.processo.pasta, pasta)


# ── Feedback ─────────────────────────────────────────────────────────────────


class FeedbackTest(BaseViewTest):

    def setUp(self):
        super().setUp()
        self.parecer = Parecer.objects.create(
            processo=self.processo, texto_ia="Texto."
        )

    def test_feedback_completo(self):
        resp = self.client.post(
            f"/api/processo/{self.processo.pk}/feedback/",
            data=json.dumps({"score": 85, "tags": "bom,rapido", "notas": "Muito bom!"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.parecer.refresh_from_db()
        self.assertEqual(self.parecer.feedback_score, 85)
        self.assertEqual(self.parecer.feedback_tags, "bom,rapido")

    def test_feedback_score_invalido(self):
        resp = self.client.post(
            f"/api/processo/{self.processo.pk}/feedback/",
            data=json.dumps({"score": 150}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


# ── Wizard Flow (smoke) ──────────────────────────────────────────────────────


class WizardFlowTest(BaseViewTest):

    def test_home_authenticated(self):
        resp = self.client.get("/app/")
        self.assertEqual(resp.status_code, 200)

    def test_processo_novo(self):
        resp = self.client.get("/processo/novo/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Processo.objects.filter(user=self.user).count(), 2)

    def test_wizard_view(self):
        resp = self.client.get(f"/processo/{self.processo.pk}/")
        self.assertEqual(resp.status_code, 200)

    def test_api_fase_atual(self):
        resp = self.client.get(f"/api/processo/{self.processo.pk}/fase/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["fase"], FaseProcesso.DOCUMENTOS)
        self.assertEqual(data["passo"], 1)

    def test_admissibilidade_dados_404(self):
        resp = self.client.get(f"/api/processo/{self.processo.pk}/admissibilidade/")
        self.assertEqual(resp.status_code, 404)
