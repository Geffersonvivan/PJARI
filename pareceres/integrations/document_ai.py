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

# Limite do Document AI por requisição (online processing, modo padrão)
_BATCH_SIZE = 15

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
        self.project_id = os.environ.get("GCP_PROJECT_ID", "the-experience-497319-f7")
        self.location = os.environ.get("DOCAI_LOCATION", "us")
        self.processor_id = os.environ.get("DOCAI_PROCESSOR_ID", "47e3d6dedb9ae505")
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            from google.cloud import documentai_v1 as documentai
            creds = _get_credentials()
            _log.info("[DOCAI] Credenciais: %s", "OK" if creds else "ADC (padrão)")
            opts = {"api_endpoint": f"{self.location}-documentai.googleapis.com"}
            self.client = documentai.DocumentProcessorServiceClient(
                credentials=creds,
                client_options=opts,
            )
            _log.info("[DOCAI] Client inicializado — project=%s processor=%s",
                      self.project_id, self.processor_id)
        except ImportError:
            if not DocumentAIClient._missing_warned:
                _log.error("[DOCAI] Pacote 'google-cloud-documentai' não instalado")
                DocumentAIClient._missing_warned = True
        except Exception as e:
            _log.error("[DOCAI] Erro ao inicializar: %s", e, exc_info=True)

    @property
    def disponivel(self) -> bool:
        return self.client is not None

    def ocr_pdf(self, file_path: str) -> dict | None:
        """OCR de PDF localizado no storage (default_storage)."""
        if not self.client:
            return None
        try:
            with default_storage.open(file_path, "rb") as f:
                pdf_bytes = f.read()
        except Exception as e:
            _log.error("[DOCAI] Erro lendo do storage: %s — %s", file_path, e)
            return None
        return self._ocr_bytes(pdf_bytes, label=file_path)

    def ocr_pdf_path(self, local_path: str) -> dict | None:
        """OCR de PDF em caminho local do filesystem (ingestão de normativos)."""
        if not self.client:
            return None
        try:
            with open(local_path, "rb") as f:
                pdf_bytes = f.read()
        except Exception as e:
            _log.error("[DOCAI] Erro lendo arquivo local: %s — %s", local_path, e)
            return None
        return self._ocr_bytes(pdf_bytes, label=local_path)

    def _ocr_bytes(self, pdf_bytes: bytes, label: str = "") -> dict | None:
        """
        Core do OCR: processa bytes de PDF via Document AI em lotes de
        _BATCH_SIZE páginas e consolida os resultados.

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
            import fitz  # PyMuPDF
            from google.cloud import documentai_v1 as documentai

            start = time.time()

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pages = len(doc)

            resource_name = self.client.processor_path(
                self.project_id, self.location, self.processor_id
            )

            # Dividir em lotes de _BATCH_SIZE páginas
            all_paginas = []
            all_textos = []
            all_confiancas = []

            for batch_start in range(0, total_pages, _BATCH_SIZE):
                batch_end = min(batch_start + _BATCH_SIZE, total_pages)
                _log.info("[DOCAI] Processando lote páginas %d-%d de %d — path=%s",
                          batch_start + 1, batch_end, total_pages, label)

                # Extrair lote como PDF separado
                batch_doc = fitz.open()
                batch_doc.insert_pdf(doc, from_page=batch_start, to_page=batch_end - 1)
                batch_bytes = batch_doc.tobytes()
                batch_doc.close()

                request = documentai.ProcessRequest(
                    name=resource_name,
                    raw_document=documentai.RawDocument(
                        content=batch_bytes,
                        mime_type="application/pdf",
                    ),
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
                batch_document = result.document

                all_textos.append(batch_document.text or "")

                for i, page in enumerate(batch_document.pages):
                    page_text = self._extrair_texto_pagina(batch_document.text, page)

                    block_confidences = []
                    for block in page.blocks:
                        if block.layout.confidence:
                            block_confidences.append(block.layout.confidence)

                    page_conf = (
                        sum(block_confidences) / len(block_confidences)
                        if block_confidences else 0.0
                    )
                    all_confiancas.append(page_conf)

                    all_paginas.append({
                        "numero": batch_start + i + 1,
                        "texto": page_text,
                        "confianca": round(page_conf, 3),
                    })

            doc.close()
            latency_ms = int((time.time() - start) * 1000)

            confianca_media = (
                sum(all_confiancas) / len(all_confiancas) if all_confiancas else 0.0
            )

            resultado = {
                "texto_completo": "\n".join(all_textos),
                "paginas": all_paginas,
                "confianca_media": round(confianca_media, 3),
                "total_paginas": len(all_paginas),
                "latency_ms": latency_ms,
            }

            _log.info(
                "[DOCAI] OCR OK: %d páginas (%d lotes), confiança=%.1f%%, latency=%dms, path=%s",
                len(all_paginas), -(-total_pages // _BATCH_SIZE),
                confianca_media * 100, latency_ms, label,
            )

            return resultado

        except Exception as e:
            _log.error("[DOCAI] Erro OCR: %s — path=%s", e, label, exc_info=True)
            return None

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
