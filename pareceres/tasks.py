"""
Celery tasks do pipeline de pareceres.

Routing:
- fast queue (default): fases 1-4
- heavy queue: fase 5 (gerar_parecer_task)

Cada task carrega o Processo, chama o service correspondente e
lida com erros/retries de forma padronizada.
"""

import logging

from celery import shared_task

from pareceres.estado import FaseProcesso

_log = logging.getLogger(__name__)


def _get_processo(processo_id):
    from pareceres.models import Processo
    return Processo.objects.select_related("user").get(id=processo_id)


def _handle_result(result, task_self, processo_id):
    """Trata resultado do service — retry se transitório, senão loga."""
    if result.ok:
        return result.dados
    _log.error("[TASK] Falha processo=%s: %s", processo_id, result.erro)
    return {"erro": result.erro}


@shared_task(bind=True, time_limit=480, max_retries=3,
             default_retry_delay=30, acks_late=True)
def extrair_documentos_task(self, processo_id):
    """Fase 2: Extrai dados dos PDFs via Gemini Files API."""
    from pareceres.services.service_documentos import execute

    try:
        processo = _get_processo(processo_id)
        result = execute(processo)
        return _handle_result(result, self, processo_id)
    except Exception as exc:
        _log.error("[TASK] extrair_documentos erro: %s — processo=%s", exc, processo_id)
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))


@shared_task(bind=True, time_limit=360, max_retries=3,
             default_retry_delay=20, acks_late=True)
def calcular_admissibilidade_task(self, processo_id):
    """Fase 3: Calcula tempestividade, prescrição e decadência."""
    from pareceres.services.service_admissibilidade import execute

    _log.info("[TASK] calcular_admissibilidade RECEBIDA processo=%s retry=%s",
              processo_id, self.request.retries)
    try:
        processo = _get_processo(processo_id)
        _log.info("[TASK] calcular_admissibilidade fase_atual=%s processo=%s",
                  processo.fase, processo_id)
        result = execute(processo)
        _log.info("[TASK] calcular_admissibilidade resultado ok=%s processo=%s",
                  result.ok, processo_id)
        return _handle_result(result, self, processo_id)
    except Exception as exc:
        _log.error("[TASK] calcular_admissibilidade erro: %s — processo=%s", exc, processo_id,
                   exc_info=True)
        raise self.retry(exc=exc, countdown=20 * (self.request.retries + 1))


@shared_task(bind=True, time_limit=480, max_retries=3,
             default_retry_delay=30, acks_late=True)
def extrair_teses_task(self, processo_id):
    """Fase 4a: Extrai teses defensivas do PDF."""
    from pareceres.services.service_teses import execute_extracao

    try:
        processo = _get_processo(processo_id)
        result = execute_extracao(processo)

        if result.ok and result.dados.get("skip_to_parecer"):
            # Nenhuma tese → dispara parecer direto (§425-427)
            processo.avancar_fase(FaseProcesso.PARECER)
            gerar_parecer_task.delay(processo_id)
            return {"chained": True, "motivo": result.dados.get("motivo")}

        return _handle_result(result, self, processo_id)
    except Exception as exc:
        _log.error("[TASK] extrair_teses erro: %s — processo=%s", exc, processo_id)
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))


@shared_task(bind=True, time_limit=480, max_retries=3,
             default_retry_delay=30, acks_late=True)
def analisar_teses_task(self, processo_id):
    """Fase 4b: Analisa teses via Vertex RAG + Perplexity + Gemini."""
    from pareceres.services.service_teses import execute_analise

    try:
        processo = _get_processo(processo_id)
        result = execute_analise(processo)
        return _handle_result(result, self, processo_id)
    except Exception as exc:
        _log.error("[TASK] analisar_teses erro: %s — processo=%s", exc, processo_id)
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))


@shared_task(bind=True, time_limit=600, max_retries=2, queue="heavy",
             default_retry_delay=60, acks_late=True)
def gerar_parecer_task(self, processo_id):
    """Fase 5: Gera parecer final (queue heavy — modelo pesado)."""
    from pareceres.services.service_parecer import execute

    try:
        processo = _get_processo(processo_id)
        result = execute(processo)
        return _handle_result(result, self, processo_id)
    except Exception as exc:
        _log.error("[TASK] gerar_parecer erro: %s — processo=%s", exc, processo_id)
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@shared_task(bind=True, time_limit=300, max_retries=2,
             default_retry_delay=20, acks_late=True)
def auditar_parecer_task(self, processo_id):
    """Fase 6: Auditoria/blindagem."""
    from pareceres.services.service_auditoria import execute

    _log.info("[TASK] auditar_parecer RECEBIDA processo=%s retry=%s",
              processo_id, self.request.retries)
    try:
        processo = _get_processo(processo_id)
        _log.info("[TASK] auditar_parecer fase_atual=%s processo=%s",
                  processo.fase, processo_id)
        result = execute(processo)
        _log.info("[TASK] auditar_parecer resultado ok=%s processo=%s erro=%s",
                  result.ok, processo_id, getattr(result, 'erro', None))
        return _handle_result(result, self, processo_id)
    except Exception as exc:
        _log.error("[TASK] auditar_parecer erro: %s — processo=%s", exc, processo_id,
                   exc_info=True)
        raise self.retry(exc=exc, countdown=20 * (self.request.retries + 1))
