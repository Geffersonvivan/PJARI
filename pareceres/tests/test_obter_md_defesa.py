"""
Testes de `_obter_md_defesa` — filtragem das páginas de defesa no OCR markdown.

Regressão do bug "Fase 4 não encontra teses" (#defesa-truncada): páginas cujo
conteúdo contém linhas de tracejado (letterhead/separador de peças eletrônicas)
eram fatiadas no meio quando o split era por "---", e só o pedaço com o cabeçalho
"## Página N" (o letterhead) sobrevivia ao filtro — o corpo (a tese) sumia.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from pareceres.services.service_teses import _obter_md_defesa


def _doc(md):
    return SimpleNamespace(extracao_json={"ocr_markdown": md})


# Página 110 com letterhead + linha de tracejado + a TESE logo abaixo.
MD = (
    "## Página 109\n\nConteúdo administrativo irrelevante.\n\n"
    "---\n\n"
    "## Página 110\n\n"
    "Marcelo Battirola - Advogado\nOAB/SC 13.319\n"
    "------------------------------------------------------------\n"
    "RAZÕES DO RECURSO: alega-se a nulidade por cerceamento de defesa "
    "e a prescrição do processo de suspensão do direito de dirigir.\n\n"
    "---\n\n"
    "## Página 111\n\nRequer o provimento do recurso.\n"
)


class ObterMdDefesaTests(SimpleTestCase):
    def test_todas_retorna_md_completo(self):
        self.assertEqual(_obter_md_defesa(_doc(MD), "todas"), MD)
        self.assertEqual(_obter_md_defesa(_doc(MD), ""), MD)

    def test_pagina_com_tracejado_preserva_o_corpo(self):
        res = _obter_md_defesa(_doc(MD), "110")
        # O corpo (a tese) NÃO pode ser descartado pelo tracejado interno.
        self.assertIn("RAZÕES DO RECURSO", res)
        self.assertIn("cerceamento de defesa", res)
        self.assertIn("## Página 110", res)
        # Não deve incluir páginas fora do range.
        self.assertNotIn("administrativo irrelevante", res)
        self.assertNotIn("Requer o provimento", res)

    def test_range_de_paginas(self):
        res = _obter_md_defesa(_doc(MD), "110-111")
        self.assertIn("RAZÕES DO RECURSO", res)
        self.assertIn("Requer o provimento", res)
        self.assertNotIn("administrativo irrelevante", res)

    def test_sem_ocr_retorna_none(self):
        self.assertIsNone(_obter_md_defesa(_doc(None), "110"))

    def test_pagina_inexistente_faz_fallback_md_completo(self):
        # Nenhuma página casa → fallback para o MD completo (não perde tudo).
        self.assertEqual(_obter_md_defesa(_doc(MD), "999"), MD)
