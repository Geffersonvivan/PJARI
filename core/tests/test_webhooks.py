"""Testes do webhook do Clerk — verificação de assinatura fail-closed (crítica de segurança).

O endpoint cria/deleta usuários e apaga processos em cascata. Um evento forjado
(assinatura inválida) OU o svix ausente devem ser REJEITADOS (403), nunca aceitos.
"""

import sys
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

BODY = '{"type":"user.deleted","data":{"id":"forjado"}}'


@override_settings(CLERK_WEBHOOK_SECRET="whsec_dummysecretforverification")
class ClerkWebhookFailClosedTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_assinatura_invalida_rejeitada(self):
        # Sem headers svix válidos → WebhookVerificationError → 403, sem processar.
        r = self.client.post("/webhooks/clerk/", data=BODY, content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_svix_ausente_fail_closed(self):
        # svix não instalável → precisa REJEITAR (403), nunca aceitar sem verificar.
        with patch.dict(sys.modules, {"svix.webhooks": None}):
            r = self.client.post("/webhooks/clerk/", data=BODY, content_type="application/json")
        self.assertEqual(r.status_code, 403)

    @override_settings(CLERK_WEBHOOK_SECRET="")
    def test_sem_secret_rejeitado(self):
        r = self.client.post("/webhooks/clerk/", data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 403)
