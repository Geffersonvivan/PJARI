"""Regressão: OCR com NUL byte travava a extração (Postgres rejeita \\u0000 em jsonb).

Bug em prod (processo 346): o OCR trazia um NUL na página 85, ia direto para
`Documento.extracao_json` e o `.save()` estourava — a UI ficava presa em
"FINALIZANDO EXTRACAO...". A limpeza remove NUL/controle antes de persistir.
"""

from django.test import SimpleTestCase

from pareceres.services.service_documentos import _limpar_texto, _ocr_para_markdown


class LimparTextoTest(SimpleTestCase):
    def test_remove_nul_e_controle(self):
        entrada = "Página 85\x00 texto\x07 controle"
        saida = _limpar_texto(entrada)
        self.assertNotIn("\x00", saida)
        self.assertNotIn("\x07", saida)
        self.assertEqual(saida, "Página 85 texto controle")

    def test_preserva_quebras_e_tabs(self):
        entrada = "linha1\nlinha2\tcol\rfim"
        self.assertEqual(_limpar_texto(entrada), entrada)

    def test_vazio_e_none(self):
        self.assertEqual(_limpar_texto(""), "")
        self.assertIsNone(_limpar_texto(None))

    def test_ocr_markdown_sem_nul(self):
        ocr = {"paginas": [
            {"numero": 84, "texto": "conteúdo ok"},
            {"numero": 85, "texto": "quebrado\x00 aqui"},
        ]}
        md = _ocr_para_markdown(ocr)
        self.assertNotIn("\x00", md)
        self.assertIn("## Página 85", md)
        self.assertIn("quebrado aqui", md)
