"""
Middleware de créditos — verifica se o usuário tem créditos
antes de permitir criação de novo processo.
"""

import logging

from django.http import JsonResponse
from django.shortcuts import redirect

_log = logging.getLogger(__name__)

# Paths que consomem crédito (criação de processo)
CREDIT_CHECK_PATHS = ("/processo/novo/",)


class CreditCheckMiddleware:
    """
    Intercepta criação de processos quando o usuário não tem créditos.
    Redireciona para a página de planos.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not any(request.path.endswith(p) for p in CREDIT_CHECK_PATHS):
            return self.get_response(request)

        if not hasattr(request, "user") or not request.user.is_authenticated:
            return self.get_response(request)

        # Superusers não consomem créditos
        if request.user.is_superuser:
            return self.get_response(request)

        try:
            profile = request.user.profile
        except Exception:
            return self.get_response(request)

        if profile.credits <= 0:
            if request.headers.get("Accept", "").startswith("application/json"):
                return JsonResponse(
                    {"error": "Sem créditos disponíveis. Adquira um plano para continuar."},
                    status=402,
                )
            return redirect("planos")

        return self.get_response(request)
