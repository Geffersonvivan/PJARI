"""
Integração com Google Gemini — extração de PDFs e geração de parecer.

Responsabilidades:
- Fase 1/2: Upload PDFs + extração de campos (autopreenchimento)
- Fase 5: Síntese do parecer final

Portado de P-Jari_antigo/chat/integrations/gemini.py com interface limpa.
"""

import itertools
import logging
import os
import tempfile
import threading
import time

from django.conf import settings
from django.core.files.storage import default_storage

_log = logging.getLogger(__name__)

# Limites de tamanho dos campos antes de montar o prompt
_LIMITES = {
    "admissibilidade": 10_000,
    "tabela_datas": 15_000,
    "analise_tese": 12_000,
    "tese": 3_000,
    "vertex": 6_000,
    "perplexity": 6_000,
}


def _trunc(texto: str, label: str, max_chars: int) -> str:
    """Trunca texto e loga se houver corte."""
    if not texto:
        return ""
    if len(texto) <= max_chars:
        return texto
    _log.warning("[TRUNC] %s truncado de %d -> %d chars", label, len(texto), max_chars)
    return texto[:max_chars] + f"\n... [truncado: {len(texto) - max_chars} chars omitidos]"


def _load_gemini_keys() -> list[str]:
    raw = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY", "")
    raw = raw.replace("\n", "").replace("\r", "").replace(" ", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


_gemini_keys: list[str] = _load_gemini_keys()
_gemini_cycle = itertools.cycle(_gemini_keys) if _gemini_keys else None
_gemini_lock = threading.Lock()


def _next_key() -> str:
    with _gemini_lock:
        if _gemini_cycle:
            return next(_gemini_cycle)
    return ""


def _is_transient(err_str: str) -> bool:
    """Detecta erros transitórios do Gemini que justificam retry."""
    markers = ("503", "504", "429", "UNAVAILABLE", "overloaded", "Too Many Requests", "DEADLINE_EXCEEDED")
    return any(m in err_str for m in markers)


def _file_path_str(field) -> str | None:
    """Normaliza FileField para string."""
    if not field:
        return None
    return field.name if hasattr(field, "name") else (str(field) or None)


class GeminiClient:
    def __init__(self):
        self.api_key = _next_key()
        self.client = self._make_client(self.api_key)

    @staticmethod
    def _make_client(api_key):
        if not api_key:
            return None
        from google import genai
        return genai.Client(api_key=api_key, http_options={"timeout": 90_000})

    def _rotate_key(self):
        if len(_gemini_keys) <= 1:
            return
        self.api_key = _next_key()
        self.client = self._make_client(self.api_key)
        _log.warning("GeminiClient: rotacionando API key após 429")

    def upload_file(self, file_path: str):
        """Upload de PDF para Gemini Files API. Retorna file handle ou None."""
        if not self.client or not file_path:
            return None

        path_str = _file_path_str(file_path)
        if not path_str:
            return None

        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                with default_storage.open(path_str, "rb") as f:
                    tmp.write(f.read())
                tmp_path = tmp.name

            uploaded = self.client.files.upload(file=tmp_path)

            # Poll até ACTIVE (máx 90s)
            deadline = time.time() + 90
            while uploaded.state.name != "ACTIVE" and time.time() < deadline:
                time.sleep(3)
                uploaded = self.client.files.get(name=uploaded.name)

            if uploaded.state.name != "ACTIVE":
                _log.error("Gemini file não ficou ACTIVE: %s (state=%s)", path_str, uploaded.state.name)
                return None

            return uploaded
        except Exception as e:
            _log.error("Erro upload Gemini: %s — %s", path_str, e)
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def upload_file_from_path(self, local_path: str):
        """Upload de PDF local (já no filesystem) para Gemini Files API."""
        if not self.client or not local_path:
            return None

        try:
            uploaded = self.client.files.upload(file=local_path)

            deadline = time.time() + 90
            while uploaded.state.name != "ACTIVE" and time.time() < deadline:
                time.sleep(3)
                uploaded = self.client.files.get(name=uploaded.name)

            if uploaded.state.name != "ACTIVE":
                _log.error("Gemini file não ficou ACTIVE: %s (state=%s)", local_path, uploaded.state.name)
                return None

            return uploaded
        except Exception as e:
            _log.error("Erro upload Gemini (local): %s — %s", local_path, e)
            return None

    def generate(self, model: str, contents: list, config: dict,
                 fallback_model: str | None = None, max_retries: int = 2,
                 timeout_per_call: int | None = None) -> tuple:
        """
        Chama generate_content com retry + fallback automático.
        Retorna (response, model_used).
        """
        from google.genai import types

        retry_delays = [3, 8]
        sequence = [model] * (1 + max_retries)
        if fallback_model and fallback_model != model:
            sequence.append(fallback_model)

        for attempt, current_model in enumerate(sequence):
            is_last = attempt == len(sequence) - 1
            try:
                if timeout_per_call:
                    holder = [None, None]
                    def _call(m=current_model, c=contents, cfg=config):
                        try:
                            holder[0] = self.client.models.generate_content(
                                model=m, contents=c, config=cfg
                            )
                        except Exception as exc:
                            holder[1] = exc

                    t = threading.Thread(target=_call, daemon=True)
                    t.start()
                    t.join(timeout=timeout_per_call)
                    if t.is_alive():
                        raise TimeoutError(f"Gemini {current_model} timeout ({timeout_per_call}s)")
                    if holder[1]:
                        raise holder[1]
                    response = holder[0]
                else:
                    response = self.client.models.generate_content(
                        model=current_model, contents=contents, config=config
                    )
                return response, current_model
            except Exception as e:
                err_str = str(e)
                if (isinstance(e, TimeoutError) or _is_transient(err_str)) and not is_last:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    if "429" in err_str:
                        self._rotate_key()
                    _log.warning("Gemini %s erro transitório (tentativa %d) — retry em %ds", current_model, attempt + 1, delay)
                    time.sleep(delay)
                    continue
                raise

    def log_usage(self, processo, response, fase: str, model_name: str = "", start_time: float = 0):
        """Registra uso de tokens no AuditLog."""
        if not processo or not response:
            return
        try:
            from pareceres.models import log_audit
            usage = getattr(response, "usage_metadata", None)
            latency_ms = int((time.time() - start_time) * 1000) if start_time else 0
            if usage:
                log_audit(
                    "ia_request",
                    processo=processo,
                    fase=fase,
                    provider="Gemini",
                    input_tokens=getattr(usage, "prompt_token_count", 0),
                    output_tokens=getattr(usage, "candidates_token_count", 0),
                    latency_ms=latency_ms,
                    model_name=model_name,
                )
        except Exception as e:
            _log.error("Erro ao logar tokens Gemini: %s", e)
