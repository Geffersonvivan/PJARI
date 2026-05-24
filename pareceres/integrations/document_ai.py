"""
Integração com Google Document AI — OCR de alta precisão para PDFs.

Responsabilidades:
- Fase 2: OCR do PDF consolidado antes da extração via LLM
- Retorna texto limpo + confiança do OCR por página

Usa o processor Document OCR pré-treinado (suporte a 200+ idiomas, incluindo pt-BR).
"""

import io
import json
import logging
import os
import tempfile
import time

from django.core.files.storage import default_storage

# Limite do Document AI para online processing
_MAX_PAGES = 30

_log = logging.getLogger(__name__)


def _get_credentials():
    """Carrega credenciais GCP (mesmo padrão do GCS)."""
    # Opção 1: JSON inline via env var
    creds_json = os.environ.get("GCP_CREDENTIALS_JSON", "")
    if creds_json:
        from google.oauth2 import service_account
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(info)

    # Opção 2: Arquivo (GOOGLE_APPLICATION_CREDENTIALS ou GCS_CREDENTIALS_FILE)
    creds_file = (
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("GCS_CREDENTIALS_FILE")
    )
    if creds_file and os.path.exists(creds_file):
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(creds_file)

    # Opção 3: Application Default Credentials (local dev com gcloud auth)
    return None


class DocumentAIClient:
    """Client para Google Document AI OCR."""

    _missing_warned = False

    def __init__(self):
        self.project_id = os.environ.get("GCP_PROJECT_ID", "206144381877")
        self.location = os.environ.get("DOCAI_LOCATION", "us")
        self.processor_id = os.environ.get("DOCAI_PROCESSOR_ID", "47e3d6dedb9ae505")
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            from google.cloud import documentai_v1 as documentai
            creds = _get_credentials()
            opts = {"api_endpoint": f"{self.location}-documentai.googleapis.com"}
            self.client = documentai.DocumentProcessorServiceClient(
                credentials=creds,
                client_options=opts,
            )
        except ImportError:
            if not DocumentAIClient._missing_warned:
                _log.error("Pacote 'google-cloud-documentai' não instalado")
                DocumentAIClient._missing_warned = True
        except Exception as e:
            _log.error("Erro ao inicializar Document AI: %s", e)

    @property
    def disponivel(self) -> bool:
        return self.client is not None

    def ocr_pdf(self, file_path: str) -> dict | None:
        """
        Processa PDF via Document AI OCR.

        Args:
            file_path: Caminho do arquivo no storage (ex: "uploads/doc.pdf")

        Returns:
            {
                "texto_completo": "...",
                "paginas": [{"numero": 1, "texto": "...", "confianca": 0.98}, ...],
                "confianca_media": 0.97,
                "total_paginas": 5,
                "latency_ms": 2300,
            }
            ou None em caso de erro.
        """
        if not self.client:
            return None

        try:
            from google.cloud import documentai_v1 as documentai

            start = time.time()

            # Ler PDF do storage
            with default_storage.open(file_path, "rb") as f:
                pdf_bytes = f.read()

            # Limitar a 30 páginas (limite do Document AI online processing)
            pdf_bytes = self._truncar_pdf(pdf_bytes)

            resource_name = self.client.processor_path(
                self.project_id, self.location, self.processor_id
            )

            request = documentai.ProcessRequest(
                name=resource_name,
                raw_document=documentai.RawDocument(
                    content=pdf_bytes,
                    mime_type="application/pdf",
                ),
                # Habilitar OCR de qualidade premium
                process_options=documentai.ProcessOptions(
                    ocr_config=documentai.OcrConfig(
                        enable_native_pdf_parsing=True,
                        hints=documentai.OcrConfig.Hints(
                            language_hints=["pt"],
                        ),
                    ),
                ),
            )

            result = self.client.process_document(request=request)
            document = result.document
            latency_ms = int((time.time() - start) * 1000)

            # Extrair texto e confiança por página
            paginas = []
            confiancas = []

            for i, page in enumerate(document.pages):
                # Texto da página
                page_text = self._extrair_texto_pagina(document.text, page)

                # Confiança média dos blocos da página
                block_confidences = []
                for block in page.blocks:
                    if block.layout.confidence:
                        block_confidences.append(block.layout.confidence)

                page_conf = (
                    sum(block_confidences) / len(block_confidences)
                    if block_confidences else 0.0
                )
                confiancas.append(page_conf)

                paginas.append({
                    "numero": i + 1,
                    "texto": page_text,
                    "confianca": round(page_conf, 3),
                })

            confianca_media = (
                sum(confiancas) / len(confiancas) if confiancas else 0.0
            )

            resultado = {
                "texto_completo": document.text,
                "paginas": paginas,
                "confianca_media": round(confianca_media, 3),
                "total_paginas": len(paginas),
                "latency_ms": latency_ms,
            }

            _log.info(
                "[DOCAI] OCR OK: %d páginas, confiança=%.1f%%, latency=%dms, path=%s",
                len(paginas), confianca_media * 100, latency_ms, file_path,
            )

            return resultado

        except Exception as e:
            _log.error("[DOCAI] Erro OCR: %s — path=%s", e, file_path, exc_info=True)
            return None

    @staticmethod
    def _truncar_pdf(pdf_bytes: bytes) -> bytes:
        """Trunca PDF para no máximo _MAX_PAGES páginas (limite do online processing)."""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(doc) <= _MAX_PAGES:
                doc.close()
                return pdf_bytes

            _log.info("[DOCAI] PDF tem %d páginas, truncando para %d", len(doc), _MAX_PAGES)
            # Criar novo PDF com apenas as primeiras _MAX_PAGES páginas
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, to_page=_MAX_PAGES - 1)
            out = new_doc.tobytes()
            new_doc.close()
            doc.close()
            return out
        except Exception as e:
            _log.warning("[DOCAI] Erro ao truncar PDF: %s — enviando original", e)
            return pdf_bytes

    @staticmethod
    def _extrair_texto_pagina(full_text: str, page) -> str:
        """Extrai o texto de uma página usando os text segments."""
        segments = []
        for block in page.blocks:
            for segment in block.layout.text_anchor.text_segments:
                start = int(segment.start_index) if segment.start_index else 0
                end = int(segment.end_index)
                segments.append(full_text[start:end])
        return "".join(segments)
