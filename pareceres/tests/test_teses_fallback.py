"""Testes do fallback de extração de teses (Fase 4): Gemini → Anthropic → degradação.

Cobrem a resiliência adicionada após o incidente do SDK do Gemini: se o provedor
primário cair, o secundário assume; se ambos caírem, o chamador degrada
graciosamente (não trava o front nem deixa a task em retry).
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from pareceres.services import service_teses


class _FakeProcesso:
    id = 999


class ExtrairTesesFallbackTest(SimpleTestCase):
    def setUp(self):
        self.proc = _FakeProcesso()

    def test_gemini_ok_nao_chama_anthropic(self):
        with patch.object(service_teses, "_extrair_teses_gemini", return_value="Tese 1: A"), \
             patch("pareceres.integrations.anthropic.AnthropicClient") as mock_ac:
            r = service_teses._extrair_teses_llm(self.proc, None, "todas", "md", "prompt", "sys")
            self.assertEqual(r, "Tese 1: A")
            mock_ac.assert_not_called()

    def test_gemini_falha_cai_para_anthropic(self):
        mock_client = MagicMock()
        mock_client.generate_text.return_value = "Tese 1: B"
        with patch.object(service_teses, "_extrair_teses_gemini", side_effect=RuntimeError("gemini down")), \
             patch("pareceres.integrations.anthropic.AnthropicClient", return_value=mock_client):
            r = service_teses._extrair_teses_llm(self.proc, None, "todas", "md", "prompt", "sys")
            self.assertEqual(r, "Tese 1: B")
            mock_client.generate_text.assert_called_once()

    def test_ambos_falham_retorna_none(self):
        mock_client = MagicMock()
        mock_client.generate_text.side_effect = RuntimeError("anthropic down")
        with patch.object(service_teses, "_extrair_teses_gemini", side_effect=RuntimeError("gemini down")), \
             patch("pareceres.integrations.anthropic.AnthropicClient", return_value=mock_client):
            r = service_teses._extrair_teses_llm(self.proc, None, "todas", "md", "prompt", "sys")
            self.assertIsNone(r)

    def test_sem_ocr_e_gemini_falha_nao_tenta_anthropic(self):
        # Sem MD (prompt_texto=None) não há via textual pro Anthropic → degrada (None)
        with patch.object(service_teses, "_extrair_teses_gemini", side_effect=RuntimeError("gemini down")), \
             patch("pareceres.integrations.anthropic.AnthropicClient") as mock_ac:
            r = service_teses._extrair_teses_llm(self.proc, None, "todas", None, None, "sys")
            self.assertIsNone(r)
            mock_ac.assert_not_called()
