"""
Testes da heurística `_pagina_escaneada` (Document AI Fase 2).

Regressão do bug "Fase 4 não encontra teses": peças escaneadas com um fino
rodapé de assinatura eletrônica (nome + OAB) passavam do limiar de caracteres
nativos, o OCR era pulado e o corpo (a tese) nunca era lido.
"""

from django.test import SimpleTestCase

from pareceres.integrations.document_ai import (
    _IMG_COVER_SCANNED,
    _MIN_CHARS_TEXTO_DIGITAL,
    _pagina_escaneada,
)


class _Rect:
    def __init__(self, w, h):
        self.width = w
        self.height = h


class _FakePage:
    """Duck-type mínimo de uma página PyMuPDF para a heurística."""

    def __init__(self, w=600, h=800, image_rects=None):
        self.rect = _Rect(w, h)
        # cada imagem é (xref, rect_cobertura)
        self._images = list(enumerate(image_rects or [], start=1))

    def get_images(self, full=True):
        # PyMuPDF retorna tuplas cujo [0] é o xref
        return [(xref,) for xref, _ in self._images]

    def get_image_rects(self, xref):
        for x, rect in self._images:
            if x == xref:
                return [rect]
        return []


class PaginaEscaneadaTests(SimpleTestCase):
    def test_pagina_escaneada_com_rodape_de_assinatura(self):
        # Corpo é imagem de página inteira; rodapé curto (nome + OAB) passou os 50
        # chars mas está longe dos 800 → deve ir ao OCR.
        page = _FakePage(600, 800, image_rects=[_Rect(600, 800)])
        self.assertTrue(_pagina_escaneada(page, texto_len=120))

    def test_pagina_digital_texto_abundante_nao_vai_ao_ocr(self):
        # Muito texto nativo → confia mesmo com imagem grande (letterhead/marca).
        page = _FakePage(600, 800, image_rects=[_Rect(600, 800)])
        self.assertFalse(_pagina_escaneada(page, texto_len=_MIN_CHARS_TEXTO_DIGITAL + 1))

    def test_pagina_digital_com_logo_pequeno(self):
        # Logo cobrindo pouca área + texto curto → não é scan.
        page = _FakePage(600, 800, image_rects=[_Rect(100, 60)])
        self.assertFalse(_pagina_escaneada(page, texto_len=120))

    def test_pagina_sem_imagem(self):
        page = _FakePage(600, 800, image_rects=[])
        self.assertFalse(_pagina_escaneada(page, texto_len=120))

    def test_limiar_de_cobertura(self):
        area = 600 * 800
        # Exatamente no limiar → escaneada
        lado = (area * _IMG_COVER_SCANNED) ** 0.5
        page = _FakePage(600, 800, image_rects=[_Rect(lado, lado)])
        self.assertTrue(_pagina_escaneada(page, texto_len=100))

    def test_excecao_no_pymupdf_nao_forca_ocr(self):
        class _Boom:
            rect = _Rect(600, 800)

            def get_images(self, full=True):
                raise RuntimeError("boom")

        self.assertFalse(_pagina_escaneada(_Boom(), texto_len=100))
