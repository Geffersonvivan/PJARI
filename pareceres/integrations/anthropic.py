"""
Integração com Anthropic Claude — auditoria/blindagem e extração unificada.

Responsabilidades:
- Fase 6: Auditoria cruzada (blindagem score)
- Fallback de extração Fase 1/2 quando Gemini falha

Portado de P-Jari_antigo/chat/integrations/anthropic.py.
"""

import base64
import json
import logging
import os
import time

_log = logging.getLogger(__name__)

_LIMITES = {
    "admissibilidade": 8_000,
    "tabela_datas": 8_000,
    "analise_tese": 8_000,
    "tese": 3_000,
    "vertex": 4_000,
    "perplexity": 4_000,
}


def _trunc(texto: str, label: str, max_chars: int) -> str:
    if not texto:
        return ""
    if len(texto) <= max_chars:
        return texto
    _log.warning("[TRUNC] %s truncado de %d -> %d chars", label, len(texto), max_chars)
    return texto[:max_chars] + f"\n... [truncado: {len(texto) - max_chars} chars omitidos]"


def _extract_json_block(text: str) -> dict:
    """Extrai o primeiro objeto JSON completo de um texto."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError("Nenhum JSON encontrado na resposta")

    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("JSON incompleto na resposta")


def _retry_on_rate_limit(fn, max_retries=4, base_delay=20):
    """Retry com backoff para rate limit (429)."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            err_str = str(e)
            is_rate_limit = any(m in err_str for m in ("rate_limit", "RateLimitError", "429"))
            if is_rate_limit and attempt < max_retries:
                delay = base_delay * (attempt + 1)
                _log.warning("Rate limit (tentativa %d/%d), aguardando %ds", attempt + 1, max_retries, delay)
                time.sleep(delay)
            else:
                raise


class AnthropicClient:
    _missing_warned = False

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        try:
            from anthropic import Anthropic
            if self.api_key:
                self.client = Anthropic(
                    api_key=self.api_key,
                    timeout=120.0,  # 120s max per request
                )
            else:
                self.client = None
                if not AnthropicClient._missing_warned:
                    _log.error("ANTHROPIC_API_KEY ausente")
                    AnthropicClient._missing_warned = True
        except ImportError:
            self.client = None
            if not AnthropicClient._missing_warned:
                _log.error("Pacote 'anthropic' não instalado")
                AnthropicClient._missing_warned = True

    def pdf_to_images(self, file_path: str, max_pages: int = 15) -> list[dict]:
        """Converte PDF em imagens JPEG para envio ao Claude (evita 413 de PDFs grandes)."""
        import fitz  # PyMuPDF
        from django.core.files.storage import default_storage

        images = []
        try:
            with default_storage.open(file_path, "rb") as f:
                doc = fitz.open(stream=f.read(), filetype="pdf")

            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                pix = page.get_pixmap(dpi=200)
                jpeg_bytes = pix.tobytes("jpeg", jpg_quality=80)
                b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
                images.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                })
            doc.close()
        except Exception as e:
            _log.error("Erro convertendo PDF para imagens: %s — %s", file_path, e)
        return images

    def extract_documentos(self, processo, docs_dict: dict, prompt: str) -> str | None:
        """
        Extrai campos da Fase 2 dos PDFs via Claude (imagens JPEG).

        Args:
            processo: Processo instance (para logging)
            docs_dict: {"consolidado": "uploads/...", "autuacao": "uploads/...", ...}
            prompt: prompt de extração (compartilhado com Gemini)

        Returns:
            Texto da resposta do Claude ou None em caso de erro.
        """
        if not self.client:
            _log.warning("[ANTHROPIC] Client não disponível")
            return None

        content = []
        for tipo in ("consolidado", "autuacao", "ata"):
            path = docs_dict.get(tipo)
            if not path:
                continue
            _log.info("[ANTHROPIC] Convertendo PDF tipo=%s path=%s", tipo, path)
            images = self.pdf_to_images(path)
            _log.info("[ANTHROPIC] PDF tipo=%s -> %d imagens", tipo, len(images))
            if images:
                content.append({"type": "text", "text": f"--- {tipo.upper()} ---"})
                content.extend(images)

        if not content:
            _log.warning("[ANTHROPIC] Nenhuma imagem extraída dos PDFs — docs_dict=%s", docs_dict)
            return None

        content.append({"type": "text", "text": prompt})

        try:
            start_time = time.time()

            def _call():
                return self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4096,
                    messages=[{"role": "user", "content": content}],
                )

            response = _retry_on_rate_limit(_call)
            latency_ms = int((time.time() - start_time) * 1000)

            from pareceres.models import log_ia_request
            log_ia_request(
                processo,
                fase="Extração F2",
                provider="Anthropic",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=latency_ms,
                model_name="claude-sonnet-4-20250514",
            )

            texto = response.content[0].text
            _log.info("[ANTHROPIC] Extração OK processo=%s tokens_in=%d tokens_out=%d latency=%dms",
                      processo.id, response.usage.input_tokens, response.usage.output_tokens, latency_ms)
            return texto
        except Exception as e:
            _log.error("[ANTHROPIC] Erro extração: %s — processo=%s", e, processo.id)
            return None

    def extract_from_text(self, processo, texto_ocr: str, prompt: str) -> str | None:
        """Extrai campos usando texto OCR pré-processado (sem enviar imagens)."""
        if not self.client:
            return None

        content = f"TEXTO EXTRAÍDO DO PDF VIA OCR:\n\n{texto_ocr[:50000]}\n\n{prompt}"

        try:
            start_time = time.time()

            def _call():
                return self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": content}],
                )

            response = _retry_on_rate_limit(_call)
            latency_ms = int((time.time() - start_time) * 1000)

            from pareceres.models import log_ia_request
            log_ia_request(
                processo,
                fase="Extração F2 (OCR text)",
                provider="Anthropic",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=latency_ms,
                model_name="claude-sonnet-4-20250514",
            )

            texto = response.content[0].text
            _log.info("[ANTHROPIC] Extração OCR text OK processo=%s tokens_in=%d latency=%dms",
                      processo.id, response.usage.input_tokens, latency_ms)
            return texto
        except Exception as e:
            _log.error("[ANTHROPIC] Erro extração OCR text: %s — processo=%s", e, processo.id)
            return None

    def get_pdf_content(self, file_path) -> dict | None:
        """Codifica PDF em base64 para envio ao Claude."""
        if not file_path:
            return None
        from django.core.files.storage import default_storage
        try:
            with default_storage.open(file_path, "rb") as f:
                pdf_data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_data,
                    },
                }
        except Exception as e:
            _log.error("Erro encoding PDF para Anthropic: %s", e)
            return None

    def generate_text(self, processo, user_prompt: str, system_prompt: str = "",
                      max_tokens: int = 4096, temperature: float = 0.2,
                      fase_label: str = "Geração") -> str | None:
        """
        Geração genérica de texto com system prompt.
        Usado em Fase 3 (admissibilidade) e Fase 5 (parecer).

        Returns:
            Texto gerado ou None em caso de erro.
        """
        if not self.client:
            _log.warning("[ANTHROPIC] Client não disponível")
            return None

        kwargs = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        try:
            start_time = time.time()

            def _call():
                return self.client.messages.create(**kwargs)

            response = _retry_on_rate_limit(_call)
            latency_ms = int((time.time() - start_time) * 1000)

            from pareceres.models import log_ia_request
            log_ia_request(
                processo,
                fase=fase_label,
                provider="Anthropic",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=latency_ms,
                model_name="claude-sonnet-4-20250514",
            )

            texto = response.content[0].text
            _log.info("[ANTHROPIC] %s OK processo=%s tokens_in=%d tokens_out=%d latency=%dms",
                      fase_label, processo.id, response.usage.input_tokens,
                      response.usage.output_tokens, latency_ms)
            return texto
        except Exception as e:
            _log.error("[ANTHROPIC] Erro %s: %s — processo=%s", fase_label, e, processo.id)
            return None

    def audit_parecer(self, processo, parecer_texto: str, admissibilidade_texto: str,
                      checklist_items: list[str]) -> dict:
        """
        Fase 6: Auditoria cruzada do parecer.
        Retorna dict com score (0-100), detalhes e checklist JSON.
        """
        if not self.client:
            return {"score": None, "detalhes": "Anthropic não configurado", "checklist": []}

        prompt = (
            "Você é um auditor jurídico especializado em pareceres JARI.\n"
            "Analise o parecer abaixo e verifique cada item do checklist.\n\n"
            f"PARECER:\n{_trunc(parecer_texto, 'parecer', 15_000)}\n\n"
            f"ADMISSIBILIDADE:\n{_trunc(admissibilidade_texto, 'admissibilidade', _LIMITES['admissibilidade'])}\n\n"
            f"CHECKLIST:\n" + "\n".join(f"- {item}" for item in checklist_items) + "\n\n"
            "Responda com JSON: {score: 0-100, itens: [{item: str, ok: bool, obs: str}], resumo: str}"
        )

        try:
            start_time = time.time()

            def _call():
                return self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )

            response = _retry_on_rate_limit(_call)
            latency_ms = int((time.time() - start_time) * 1000)

            text = response.content[0].text
            result = _extract_json_block(text)

            # Log
            from pareceres.models import log_ia_request
            log_ia_request(
                processo,
                fase="Auditoria F6",
                provider="Anthropic",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=latency_ms,
                model_name="claude-sonnet-4-20250514",
            )

            return {
                "score": result.get("score"),
                "detalhes": result.get("resumo", ""),
                "checklist": result.get("itens", []),
            }
        except Exception as e:
            _log.error("Erro auditoria Anthropic: %s", e)
            return {"score": None, "detalhes": f"Erro: {e}", "checklist": []}
