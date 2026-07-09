"""Testes do fallback de extração de teses (Fase 4): Gemini → Anthropic → degradação.

Cobrem a resiliência adicionada após o incidente do SDK do Gemini: se o provedor
primário cair, o secundário assume; se ambos caírem, o chamador degrada
graciosamente (não trava o front nem deixa a task em retry).
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from pareceres.models import AnaliseTese, Processo
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

    def test_gemini_falha_anthropic_retorna_none_degrada(self):
        # #3: generate_text retorna None em erro (não levanta). _extrair_teses_llm
        # deve tratar como falha → retornar None (degradação), NÃO "" (que viraria
        # a mensagem errada "Nenhuma tese defensiva identificada").
        mock_client = MagicMock()
        mock_client.generate_text.return_value = None
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


class PlaceholderTeseTest(SimpleTestCase):
    def test_ambos_placeholders_reconhecidos(self):
        # #1: os DOIS títulos-placeholder (vazio E indisponível) precisam ser
        # reconhecidos, senão a mensagem de erro vira uma "tese" real na tela.
        self.assertTrue(service_teses.is_placeholder_tese(service_teses.TITULO_SEM_TESES))
        self.assertTrue(service_teses.is_placeholder_tese(service_teses.TITULO_EXTRACAO_INDISPONIVEL))
        self.assertFalse(service_teses.is_placeholder_tese("Tese 1: cerceamento de defesa."))
        self.assertFalse(service_teses.is_placeholder_tese(""))
        self.assertFalse(service_teses.is_placeholder_tese(None))


class GenerateTextForwardsParamsTest(SimpleTestCase):
    def test_generate_text_encaminha_temperature_e_modelo(self):
        # #2: temperature deve chegar no messages.create (antes era descartado).
        from pareceres.integrations.anthropic import AnthropicClient, SONNET_MODEL
        client = AnthropicClient.__new__(AnthropicClient)
        resp = MagicMock()
        resp.content = [MagicMock(text="ok")]
        resp.usage = MagicMock(input_tokens=1, output_tokens=1)
        msgs = MagicMock()
        msgs.create.return_value = resp
        client.client = MagicMock(messages=msgs)
        with patch("pareceres.models.log_ia_request"):
            out = client.generate_text(MagicMock(id=1), "prompt", system_prompt="sys",
                                       temperature=0.1, max_tokens=100)
        self.assertEqual(out, "ok")
        kw = msgs.create.call_args.kwargs
        self.assertEqual(kw["temperature"], 0.1)
        self.assertEqual(kw["model"], SONNET_MODEL)


class RelatorioPrefetchTest(TestCase):
    def test_montar_item_usa_cache_do_prefetch(self):
        # #5: com prefetch_related("teses"), processo.teses.all() não deve disparar
        # query nova (o antigo .order_by("ordem") disparava → N+1 no relatório).
        user = User.objects.create_user(username="rel", password="x")
        proc = Processo.objects.create(user=user, pa="PA-REL/2025")
        AnaliseTese.objects.create(processo=proc, ordem=2, titulo="T2")
        AnaliseTese.objects.create(processo=proc, ordem=1, titulo="T1")
        p = Processo.objects.prefetch_related("teses").get(pk=proc.pk)
        with self.assertNumQueries(0):
            teses = list(p.teses.all())
        self.assertEqual([t.ordem for t in teses], [1, 2])  # Meta.ordering = ["ordem"]
