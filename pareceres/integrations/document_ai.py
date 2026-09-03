"""
Integração com Google Document AI — OCR de alta precisão para PDFs.

Responsabilidades:
- Fase 2: OCR do PDF consolidado antes da extração via LLM
- Retorna texto limpo + confiança do OCR por página

Usa o processor Document OCR pré-treinado (suporte a 200+ idiomas, incluindo pt-BR).
"""

import json
import logging
import os
import time

from django.core.files.storage import default_storage

# Limite do Document AI por requisição (online processing, modo padrão)
_BATCH_SIZE = 15

# Uma página é considerada "texto nativo" se o PyMuPDF extrai ao menos este número
# de caracteres — evita o OCR (gargalo por página) em PDFs digitais. Páginas
# escaneadas (imagem pura) retornam ~0 chars e continuam indo para o Document AI.
_MIN_CHARS_PAGINA = 50

# Acima deste número de caracteres nativos a página é claramente digital: confiamos
# no texto e não checamos imagem (evita falso-positivo por marca d'água/letterhead
# de página inteira). Rodapés de assinatura eletrônica ficam MUITO abaixo disto.
_MIN_CHARS_TEXTO_DIGITAL = 800

# Fração da área da página coberta por imagem a partir da qual a página é tratada
# como escaneada — vai para o OCR mesmo que tenha uma fina camada de texto nativo.
_IMG_COVER_SCANNED = 0.5

# Máximo de lotes de OCR processados em paralelo (I/O de rede). Configurável.
_DOCAI_MAX_PARALLEL = int(os.environ.get("DOCAI_MAX_PARALLEL", "6"))

_log = logging.getLogger(__name__)


def _pagina_escaneada(page, texto_len: int) -> bool:
    """True se a página parece ser uma imagem escaneada — deve ir ao OCR mesmo
    que tenha uma fina camada de texto nativo.

    Peças eletrônicas (SEI/PJe/eProc) escaneiam o corpo como imagem e carimbam um
    rodapé de assinatura ("assinado eletronicamente por FULANO, OAB/SC …") que,
    sozinho, passa do limiar de caracteres. Sem esta checagem o OCR seria pulado e
    a tese (no corpo escaneado) nunca seria lida — a Fase 4 então "não encontra
    teses", vendo só a repetição do nome/OAB do rodapé.

    Texto abundante (>= _MIN_CHARS_TEXTO_DIGITAL) indica página nativa de verdade:
    confiamos nela e não checamos imagem, evitando falso-positivo por marca d'água
    ou letterhead de página inteira.
    """
    if texto_len >= _MIN_CHARS_TEXTO_DIGITAL:
        return False
    try:
        page_area = abs(page.rect.width * page.rect.height)
        if page_area <= 0:
            return False
        img_area = 0.0
        for img in page.get_images(full=True):
            for rect in page.get_image_rects(img[0]):
                img_area += abs(rect.width * rect.height)
        return (img_area / page_area) >= _IMG_COVER_SCANNED
    except Exception:
        # Na dúvida, não força OCR (preserva o comportamento antigo p/ a página).
        return False


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
            import concurrent.futures
            import fitz  # PyMuPDF
            from google.cloud import documentai_v1 as documentai

            start = time.time()

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pages = len(doc)

            # ── Passo A: classificar cada página — texto nativo vs. precisa OCR ──
            # Páginas com camada de texto (PDFs digitais) são exatas e de graça;
            # só as escaneadas (imagem) vão para o Document AI. Preserva 100% do
            # conteúdo — nada de perder página escaneada num PDF misto.
            paginas_finais = [None] * total_pages   # dict por índice de página
            faltantes = []                          # índices que precisam de OCR
            nativo_faltantes = {}                   # índice -> texto nativo (fallback)
            for i, page in enumerate(doc):
                texto = page.get_text("text") or ""
                n_chars = len(texto.strip())
                # Página escaneada com fina camada de texto (rodapé de assinatura
                # eletrônica: "assinado por FULANO, OAB/SC …") passa do limiar de
                # caracteres mas o corpo (a tese) é imagem. Sem esta checagem o OCR
                # seria pulado e a defesa nunca seria lida. (#teses-vazias)
                if n_chars >= _MIN_CHARS_PAGINA and not _pagina_escaneada(page, n_chars):
                    paginas_finais[i] = {"numero": i + 1, "texto": texto, "confianca": 1.0}
                else:
                    faltantes.append(i)
                    # Guarda o nativo p/ o Passo D decidir "manter o mais longo":
                    # em PDF pesquisável (imagem + camada de texto COMPLETA) o nativo
                    # já é bom e o re-OCR não deve encurtá-lo. Só páginas realmente
                    # escaneadas (nativo = rodapé) ganham conteúdo com o OCR.
                    nativo_faltantes[i] = texto

            # ── Atalho: PDF 100% nativo → nenhum OCR ────────────────────────
            if not faltantes:
                doc.close()
                latency_ms = int((time.time() - start) * 1000)
                _log.info("[DOCAI] PDF 100%% texto nativo: %d páginas — OCR PULADO, "
                          "latency=%dms, path=%s", total_pages, latency_ms, label)
                return {
                    "texto_completo": "\n".join(p["texto"] for p in paginas_finais),
                    "paginas": paginas_finais,
                    "confianca_media": 1.0,
                    "total_paginas": total_pages,
                    "latency_ms": latency_ms,
                }

            resource_name = self.client.processor_path(
                self.project_id, self.location, self.processor_id
            )

            # ── Passo B: pré-fatiar SÓ as páginas faltantes em lotes ────────
            # (PyMuPDF não é thread-safe p/ acesso concorrente ao mesmo doc, então
            #  o fatiamento é sequencial; o paralelismo fica só nas chamadas de rede.)
            lotes = []  # (indices_originais, batch_bytes)
            for k in range(0, len(faltantes), _BATCH_SIZE):
                chunk = faltantes[k:k + _BATCH_SIZE]
                batch_doc = fitz.open()
                for pno in chunk:
                    batch_doc.insert_pdf(doc, from_page=pno, to_page=pno)
                lotes.append((chunk, batch_doc.tobytes()))
                batch_doc.close()
            doc.close()

            def _process_lote(chunk, batch_bytes):
                request = documentai.ProcessRequest(
                    name=resource_name,
                    raw_document=documentai.RawDocument(
                        content=batch_bytes, mime_type="application/pdf",
                    ),
                    process_options=documentai.ProcessOptions(
                        ocr_config=documentai.OcrConfig(
                            enable_native_pdf_parsing=True,
                            hints=documentai.OcrConfig.Hints(language_hints=["pt"]),
                        ),
                    ),
                )
                return chunk, self.client.process_document(request=request).document

            # ── Passo C: processar os lotes EM PARALELO (I/O de rede) ───────
            workers = max(1, min(len(lotes), _DOCAI_MAX_PARALLEL))
            resultados = []
            if workers == 1:
                for chunk, bb in lotes:
                    resultados.append(_process_lote(chunk, bb))
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = [ex.submit(_process_lote, chunk, bb) for chunk, bb in lotes]
                    for fut in futs:
                        resultados.append(fut.result())

            # ── Passo D: mapear cada página OCR de volta ao índice original ──
            ocr_confs = []
            for chunk, batch_document in resultados:
                for j, page in enumerate(batch_document.pages):
                    if j >= len(chunk):
                        break
                    orig_i = chunk[j]
                    page_text = self._extrair_texto_pagina(batch_document.text, page)
                    block_confidences = [
                        b.layout.confidence for b in page.blocks if b.layout.confidence
                    ]
                    page_conf = (
                        sum(block_confidences) / len(block_confidences)
                        if block_confidences else 0.0
                    )
                    # Não-destrutivo: se o texto nativo era MAIOR que o do OCR, a
                    # página já tinha camada de texto completa (PDF pesquisável) —
                    # mantém o nativo. O OCR só prevalece quando RECUPERA conteúdo
                    # (corpo escaneado que faltava). Evita encurtar/degradar.
                    nativo = nativo_faltantes.get(orig_i, "")
                    if len(nativo.strip()) > len(page_text.strip()):
                        page_text = nativo
                        page_conf = 1.0
                    ocr_confs.append(page_conf)
                    paginas_finais[orig_i] = {
                        "numero": orig_i + 1,
                        "texto": page_text,
                        "confianca": round(page_conf, 3),
                    }

            # Fallback defensivo: se alguma página não voltou, mantém vazia
            paginas_finais = [
                p or {"numero": i + 1, "texto": "", "confianca": 0.0}
                for i, p in enumerate(paginas_finais)
            ]

            latency_ms = int((time.time() - start) * 1000)
            confianca_media = (
                sum(p["confianca"] for p in paginas_finais) / total_pages
                if total_pages else 0.0
            )

            resultado = {
                "texto_completo": "\n".join(p["texto"] for p in paginas_finais),
                "paginas": paginas_finais,
                "confianca_media": round(confianca_media, 3),
                "total_paginas": total_pages,
                "latency_ms": latency_ms,
            }

            _log.info(
                "[DOCAI] OCR OK: %d páginas (%d nativas, %d via OCR em %d lote(s)/%d paralelo), "
                "confiança=%.1f%%, latency=%dms, path=%s",
                total_pages, total_pages - len(faltantes), len(faltantes),
                len(lotes), workers, confianca_media * 100, latency_ms, label,
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
