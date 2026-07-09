"""Testes do endurecimento do AuditLog (log_audit / log_ia_request).

Cobre os 4 fixes:
  1. Falha na gravação é LOGADA, não engolida em silêncio.
  2. log_ia_request tem assinatura tipada (typo vira TypeError).
  3. Despacho assíncrono via Celery quando AUDIT_ASYNC, com fallback síncrono.
  4. Todos os sites de IA passam pelo choke point tipado log_ia_request.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from pareceres.models import AuditLog, gravar_audit_sync, log_audit, log_ia_request


@override_settings(AUDIT_ASYNC=False)
class LogAuditSyncTest(TestCase):
    def test_grava_ia_request_com_campos_corretos(self):
        log_ia_request(
            None, fase="Extração F2", provider="Anthropic",
            input_tokens=120, output_tokens=45, latency_ms=800,
            model_name="claude-sonnet-4-6",
        )
        log = AuditLog.objects.get(categoria="ia_request")
        self.assertEqual(log.provider, "Anthropic")
        self.assertEqual(log.input_tokens, 120)
        self.assertEqual(log.output_tokens, 45)
        self.assertEqual(log.latency_ms, 800)
        self.assertEqual(log.fase, "Extração F2")

    def test_ia_request_sem_tokens_e_valido(self):
        # RAG/Vertex não são cobrados por token — devem passar sem input/output.
        log_ia_request(None, fase="RAG Local", provider="RAG-pgvector",
                       latency_ms=12, dados={"results_count": 3})
        log = AuditLog.objects.get(categoria="ia_request", provider="RAG-pgvector")
        self.assertEqual(log.input_tokens, 0)
        self.assertEqual(log.dados, {"results_count": 3})

    def test_kwarg_invalido_vira_typeerror(self):
        # Fix 2: um typo (imput_tokens) explode na hora, não vira token=0 silencioso.
        with self.assertRaises(TypeError):
            log_ia_request(None, fase="X", provider="Anthropic", imput_tokens=99)

    def test_falha_na_gravacao_e_logada_nao_engolida(self):
        # Fix 1: create quebra -> logger.exception é chamado, sem propagar exceção.
        payload = {"categoria": "erro", "fase": "x", "dados": {}}
        with patch("pareceres.models.AuditLog.objects.create", side_effect=RuntimeError("boom")), \
             patch("pareceres.models.logger") as mock_logger:
            gravar_audit_sync(payload)  # não deve levantar
            self.assertTrue(mock_logger.exception.called)

    def test_log_audit_sync_cria_registro(self):
        log_audit("decisao", fase="admissibilidade_confirmada", dados={"ok": True})
        self.assertTrue(AuditLog.objects.filter(categoria="decisao").exists())


class LogAuditAsyncTest(TestCase):
    @override_settings(AUDIT_ASYNC=True)
    def test_async_despacha_task_apos_commit(self):
        # Fix 3/#4: com AUDIT_ASYNC, delega ao Celery — mas só via on_commit
        # (o worker só insere depois do commit). captureOnCommitCallbacks simula.
        with patch("pareceres.tasks.gravar_audit_task.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                log_ia_request(None, fase="Extração F2", provider="Gemini",
                               input_tokens=10, output_tokens=5)
            self.assertEqual(mock_delay.call_count, 1)
            payload = mock_delay.call_args[0][0]
            self.assertEqual(payload["categoria"], "ia_request")
            self.assertEqual(payload["provider"], "Gemini")
            self.assertEqual(payload["input_tokens"], 10)
        self.assertFalse(AuditLog.objects.filter(categoria="ia_request").exists())

    @override_settings(AUDIT_ASYNC=True)
    def test_async_indisponivel_faz_fallback_sincrono(self):
        # Broker fora do ar -> cai no gravar_audit_sync, registro é criado mesmo assim.
        with patch("pareceres.tasks.gravar_audit_task.delay", side_effect=OSError("no broker")), \
             patch("pareceres.models.logger"):
            with self.captureOnCommitCallbacks(execute=True):
                log_ia_request(None, fase="Extração F2", provider="Gemini")
        self.assertTrue(AuditLog.objects.filter(categoria="ia_request").exists())
