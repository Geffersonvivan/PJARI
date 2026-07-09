"""Testes do helper chamar_com_fallback (#6/#8)."""

from django.test import SimpleTestCase

from pareceres.services.llm import chamar_com_fallback


class ChamarComFallbackTest(SimpleTestCase):
    def test_primario_ok_nao_chama_fallback(self):
        chamado = {"fb": False}

        def fb():
            chamado["fb"] = True
            return "B"

        self.assertEqual(chamar_com_fallback(lambda: "A", fb), "A")
        self.assertFalse(chamado["fb"])

    def test_primario_levanta_cai_pro_fallback(self):
        def prim():
            raise RuntimeError("down")

        self.assertEqual(chamar_com_fallback(prim, lambda: "B"), "B")

    def test_primario_none_cai_pro_fallback(self):
        self.assertEqual(chamar_com_fallback(lambda: None, lambda: "B"), "B")

    def test_ambos_falham_retorna_none(self):
        self.assertIsNone(chamar_com_fallback(lambda: None, lambda: None))
        self.assertIsNone(chamar_com_fallback(lambda: (_ for _ in ()).throw(RuntimeError()),
                                              lambda: None))

    def test_string_vazia_e_sucesso_nao_cai_pro_fallback(self):
        # "" é resultado VÁLIDO (ex.: LLM não achou teses) — não deve tentar o fallback.
        chamado = {"fb": False}

        def fb():
            chamado["fb"] = True
            return "B"

        self.assertEqual(chamar_com_fallback(lambda: "", fb), "")
        self.assertFalse(chamado["fb"])
