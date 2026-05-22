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
        """Carrega sentence-transformers para gerar embeddings."""
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            self.stdout.write("Modelo de embeddings carregado: all-MiniLM-L6-v2")

            def embed(text):
                vec = model.encode(text, show_progress_bar=False)
                return vec.tolist()

            return embed
        except ImportError:
            self.stderr.write(
                "sentence-transformers nao instalado. "
                "Embeddings serao gerados posteriormente."
            )
            return None

    def _extract_chunks(self, pdf_path, chunk_size, overlap):
        """Extrai texto do PDF e divide em chunks."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            self.stderr.write("PyMuPDF nao instalado. Instale com: pip install PyMuPDF")
            return []

        chunks = []
        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text").strip()
            if not text:
                continue

            words = text.split()
            if len(words) <= chunk_size:
                chunks.append((page_num + 1, text))
            else:
                # Dividir em chunks com overlap
                i = 0
                while i < len(words):
                    chunk_words = words[i:i + chunk_size]
                    chunk_text = " ".join(chunk_words)
                    chunks.append((page_num + 1, chunk_text))
                    i += chunk_size - overlap

        doc.close()
        return chunks
