"""
Management command para ingestão de documentos normativos (PDFs) na base RAG.

Uso:
    python manage.py ingerir_normativos /caminho/para/pdfs/
    python manage.py ingerir_normativos /caminho/para/pdfs/ --chunk-size 500 --overlap 50

Processo:
1. Lê cada PDF do diretório
2. Extrai texto por página via PyMuPDF
3. Divide em chunks com overlap
4. Gera embeddings via sentence-transformers
5. Salva no model DocumentoNormativo
"""

import os

from django.core.management.base import BaseCommand

from pareceres.models import DocumentoNormativo


class Command(BaseCommand):
    help = "Ingere PDFs normativos na base DocumentoNormativo para RAG vetorial."

    def add_arguments(self, parser):
        parser.add_argument("diretorio", type=str, help="Caminho do diretório com PDFs")
        parser.add_argument("--chunk-size", type=int, default=500, help="Tamanho do chunk em palavras")
        parser.add_argument("--overlap", type=int, default=50, help="Overlap entre chunks em palavras")
        parser.add_argument("--clear", action="store_true", help="Limpa base antes de ingerir")

    def handle(self, *args, **options):
        diretorio = options["diretorio"]
        chunk_size = options["chunk_size"]
        overlap = options["overlap"]

        if not os.path.isdir(diretorio):
            self.stderr.write(f"Diretorio nao encontrado: {diretorio}")
            return

        if options["clear"]:
            count = DocumentoNormativo.objects.count()
            DocumentoNormativo.objects.all().delete()
            self.stdout.write(f"Base limpa ({count} registros removidos).")

        # Carregar embedding model
        embed_fn = self._load_embed_model()

        pdfs = [f for f in os.listdir(diretorio) if f.lower().endswith(".pdf")]
        self.stdout.write(f"Encontrados {len(pdfs)} PDFs em {diretorio}")

        total = 0
        for pdf_name in sorted(pdfs):
            pdf_path = os.path.join(diretorio, pdf_name)
            chunks = self._extract_chunks(pdf_path, chunk_size, overlap)
            self.stdout.write(f"  {pdf_name}: {len(chunks)} chunks")

            for pagina, texto in chunks:
                embedding = None
                if embed_fn:
                    embedding = embed_fn(texto)

                DocumentoNormativo.objects.create(
                    nome_arquivo=pdf_name,
                    pagina_inicio=pagina,
                    texto=texto,
                    embedding=embedding,
                )
                total += 1

        self.stdout.write(self.style.SUCCESS(f"Ingestao completa: {total} fragmentos criados."))

    def _load_embed_model(self):
        """
        Retorna função de embedding usando o MESMO modelo da busca
        (pareceres.integrations.vertex.embed_texts), garantindo que
        ingestão e query produzam vetores compatíveis.
        """
        try:
            from pareceres.integrations.vertex import _MODEL_NAME, embed_texts
            # Força o carregamento do modelo já aqui (falha cedo se faltar dep)
            embed_texts(["_warmup_"])
            self.stdout.write(f"Modelo de embeddings carregado: {_MODEL_NAME}")
            return lambda text: embed_texts([text])[0]
        except (ImportError, RuntimeError) as e:
            self.stderr.write(
                f"sentence-transformers indisponivel ({e}). "
                "Embeddings nao serao gerados."
            )
            return None

    # Abaixo deste total de caracteres consideramos o PDF "escaneado" → OCR
    MIN_CHARS_TEXTO = 100

    def _extract_chunks(self, pdf_path, chunk_size, overlap):
        """Extrai texto do PDF (nativo, com fallback OCR) e divide em chunks."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            self.stderr.write("PyMuPDF nao instalado. Instale com: pip install PyMuPDF")
            return []

        # 1. Extração nativa (camada de texto do PDF)
        doc = fitz.open(pdf_path)
        paginas = []
        total_chars = 0
        for page_num in range(len(doc)):
            text = doc[page_num].get_text("text").strip()
            paginas.append((page_num + 1, text))
            total_chars += len(text)
        doc.close()

        # 2. PDF sem texto extraível (escaneado) → OCR via Document AI
        if total_chars < self.MIN_CHARS_TEXTO:
            self.stdout.write(self.style.WARNING(
                f"    sem texto nativo ({total_chars} chars) — tentando OCR via Document AI..."
            ))
            ocr_paginas = self._ocr_paginas(pdf_path)
            if ocr_paginas:
                paginas = ocr_paginas
                ocr_chars = sum(len(t) for _, t in ocr_paginas)
                self.stdout.write(self.style.SUCCESS(
                    f"    OCR ok: {ocr_chars} chars extraidos"
                ))
            else:
                self.stderr.write("    OCR indisponivel/falhou — PDF ignorado.")
                return []

        # 3. Chunking com overlap
        chunks = []
        for page_num, text in paginas:
            if not text:
                continue
            words = text.split()
            if len(words) <= chunk_size:
                chunks.append((page_num, text))
            else:
                i = 0
                while i < len(words):
                    chunk_words = words[i:i + chunk_size]
                    chunks.append((page_num, " ".join(chunk_words)))
                    i += chunk_size - overlap

        return chunks

    def _ocr_paginas(self, pdf_path):
        """Roda Document AI OCR num PDF escaneado. Retorna [(num, texto), ...] ou None."""
        try:
            from pareceres.integrations.document_ai import DocumentAIClient
        except ImportError:
            return None
        client = DocumentAIClient()
        if not client.disponivel:
            return None
        resultado = client.ocr_pdf_path(pdf_path)
        if not resultado or not resultado.get("paginas"):
            return None
        return [(p["numero"], p["texto"]) for p in resultado["paginas"]]
