"""
Middleware que exige aceite dos termos de uso antes de usar o app.
"""

from django.http import JsonResponse
from django.shortcuts import redirect

from .models import TermoAceite

VERSAO_VIGENTE = "1.0"

# Paths que não exigem aceite
EXEMPT_PATHS = (
    "/admin/",
    "/health/",
    "/webhooks/",
    "/termos/",
    "/logout/",
    "/",
    "/auth/",
    "/static/",
    "/planos/",
    "/checkout/",
)


class RequireTermosMiddleware:
    """Bloqueia acesso ao app se o usuário não aceitou os termos vigentes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_exempt(request):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return self.get_response(request)

        if request.user.is_superuser:
            return self.get_response(request)

        if TermoAceite.usuario_aceitou(request.user, VERSAO_VIGENTE):
            return self.get_response(request)

        # API retorna 403, HTML redireciona
        if request.path.startswith("/api/"):
            return JsonResponse(
                {"error": "Aceite dos termos de uso necessário.", "redirect": "/termos/"},
                status=403,
            )
        return redirect("core:termos_visualizar")

    def _is_exempt(self, request):
        return any(request.path.startswith(p) for p in EXEMPT_PATHS)
