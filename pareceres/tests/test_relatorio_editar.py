"""Testes da edição inline da síntese do relatório mensal (override manual)."""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from pareceres.models import Processo
from pareceres.views_relatorio import _montar_item


class RelatorioSinteseEditavelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("jul", password="x")
        self.client = Client()
        self.client.login(username="jul", password="x")
        self.proc = Processo.objects.create(
            user=self.user, pa="PA-206", recorrente="EVERSON", resultado_final="INDEFERIDO",
        )
        self.url = f"/api/processo/{self.proc.pk}/relatorio-sintese/"

    def test_montar_item_usa_override_quando_editado(self):
        item = _montar_item(self.proc)
        self.assertFalse(item["editado"])
        self.assertIn("Recurso contra", item["sintese"])  # texto gerado

        self.proc.relatorio_sintese_editada = "Minha síntese custom."
        self.proc.save()
        item2 = _montar_item(self.proc)
        self.assertTrue(item2["editado"])
        self.assertEqual(item2["sintese"], "Minha síntese custom.")

    def test_endpoint_salva_e_limpa(self):
        r = self.client.post(self.url, data=json.dumps({"sintese": "editado!"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["editado"])
        self.proc.refresh_from_db()
        self.assertEqual(self.proc.relatorio_sintese_editada, "editado!")

        # texto vazio → remove o override (volta ao gerado)
        r2 = self.client.post(self.url, data=json.dumps({"sintese": "   "}),
                              content_type="application/json")
        self.assertFalse(r2.json()["editado"])
        self.proc.refresh_from_db()
        self.assertEqual(self.proc.relatorio_sintese_editada, "")

    def test_endpoint_outro_user_404(self):
        outro = User.objects.create_user("outro", password="x")
        proc2 = Processo.objects.create(user=outro, pa="PA-X")
        r = self.client.post(f"/api/processo/{proc2.pk}/relatorio-sintese/",
                             data=json.dumps({"sintese": "x"}), content_type="application/json")
        self.assertEqual(r.status_code, 404)
