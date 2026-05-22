"""
Alertas por email para eventos administrativos.
Usa console backend em dev, SMTP em prod.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

_log = logging.getLogger(__name__)


def _get_admin_emails():
    """Retorna lista de emails admin configurados."""
    email = getattr(settings, "ADMIN_EMAIL", "")
    if not email:
        return []
    return [e.strip() for e in email.split(",") if e.strip()]


def alert_novo_usuario(user):
    """Alerta admin sobre novo cadastro."""
    recipients = _get_admin_emails()
    if not recipients:
        return
    try:
        send_mail(
            subject=f"[P-JARI] Novo usuário: {user.username}",
            message=f"Novo usuário cadastrado:\n\nUsername: {user.username}\nEmail: {user.email}\n",
            from_email=None,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception as e:
        _log.warning("[ALERT] Falha ao enviar email novo usuario: %s", e)


def alert_rag_miss(processo, query):
    """Alerta admin quando RAG retorna vazio (possível gap no acervo normativo)."""
    recipients = _get_admin_emails()
    if not recipients:
        return
    try:
        send_mail(
            subject=f"[P-JARI] RAG Miss — PA {processo.pa}",
            message=(
                f"RAG retornou resultado vazio.\n\n"
                f"PA: {processo.pa}\n"
                f"Query: {query}\n"
                f"Processo ID: {processo.id}\n"
            ),
            from_email=None,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception as e:
        _log.warning("[ALERT] Falha ao enviar email RAG miss: %s", e)


def alert_erro_critico(subject, message):
    """Alerta genérico para erros críticos."""
    recipients = _get_admin_emails()
    if not recipients:
        return
    try:
        send_mail(
            subject=f"[P-JARI] {subject}",
            message=message,
            from_email=None,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception as e:
        _log.warning("[ALERT] Falha ao enviar email erro critico: %s", e)
