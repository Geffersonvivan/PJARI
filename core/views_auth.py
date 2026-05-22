from django.conf import settings as django_settings
from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect, render


def landing_view(request):
    """Landing page pública. Autenticados vão direto para o app."""
    if request.user.is_authenticated:
        return redirect("pareceres:home")
    return render(request, "landing.html", {
        "CLERK_PUBLISHABLE_KEY": getattr(django_settings, "CLERK_PUBLISHABLE_KEY", ""),
    })


def auth_sync_view(request):
    """Página interceptora: grava cookie __session e redireciona para /app/."""
    return render(request, "auth_sync.html", {
        "CLERK_PUBLISHABLE_KEY": getattr(django_settings, "CLERK_PUBLISHABLE_KEY", ""),
    })


def logout_view(request):
    """Encerra sessão Django + Clerk e redireciona à LP."""
    auth_logout(request)
    response = render(request, "logout.html", {
        "CLERK_PUBLISHABLE_KEY": getattr(django_settings, "CLERK_PUBLISHABLE_KEY", ""),
    })
    response.delete_cookie("__session")
    return response
