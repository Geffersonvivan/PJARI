"""Testes de integridade das integrações — nunca fabricar/vazar conteúdo jurídico."""

from unittest.mock import patch

from django.test import SimpleTestCase

from pareceres.integrations.perplexity import PerplexityClient


class PerplexityNaoFabricaTest(SimpleTestCase):
    def test_sem_api_key_nao_fabrica_jurisprudencia(self):
        c = PerplexityClient()
        c.api_key = ""
        r = c.search_tese(None, "cerceamento de defesa")
        self.assertNotIn("REsp", r)          # nada de acórdão inventado
        self.assertIn("indispon", r.lower())  # marcador honesto

    def test_erro_nao_vaza_excecao_como_fundamentacao(self):
        c = PerplexityClient()
        c.api_key = "fake-key"
        with patch("pareceres.integrations.perplexity._get_redis", return_value=None), \
             patch("pareceres.integrations.perplexity.requests.post", side_effect=RuntimeError("stack trace secreto")):
            r = c.search_tese(None, "tese x")
        self.assertNotIn("stack trace secreto", r)
        self.assertIn("indispon", r.lower())
