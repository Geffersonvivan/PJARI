import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db import connection
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .middleware_termos import VERSAO_VIGENTE
from .models import TIER_CHOICES, TermoAceite, TierConfig, UserProfile


def health_check(request):
    """Health check — verifica DB e Redis."""
    checks = {}

    # DB
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    # Redis
    try:
        from django.core.cache import cache
        cache.set("health_check", "ok", timeout=10)
        checks["redis"] = "ok" if cache.get("health_check") == "ok" else "error"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    status = 200 if all(v == "ok" for v in checks.values()) else 503
    return JsonResponse({"status": "healthy" if status == 200 else "unhealthy", **checks}, status=status)


# ── Termos de Uso ────────────────────────────────────────────────────────────


@login_required
def termos_visualizar(request):
    """Exibe os termos de uso para aceite."""
    ja_aceitou = TermoAceite.usuario_aceitou(request.user, VERSAO_VIGENTE)
    return render(request, "core/termos.html", {
        "versao": VERSAO_VIGENTE,
        "ja_aceitou": ja_aceitou,
    })


@login_required
@require_POST
def termos_aceitar(request):
    """Registra aceite dos termos pelo usuário."""
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    if not ip:
        ip = request.META.get("REMOTE_ADDR")

    TermoAceite.objects.update_or_create(
        user=request.user,
        versao=VERSAO_VIGENTE,
        defaults={"ip_address": ip},
    )

    if request.headers.get("Content-Type") == "application/json":
        return JsonResponse({"ok": True})

    from django.shortcuts import redirect
    return redirect("pareceres:home")


# ── TierConfig (admin) ──────────────────────────────────────────────────────


@login_required
def api_tier_config(request):
    """GET: retorna tier atual. POST: atualiza (apenas superuser)."""
    config = TierConfig.load()

    if request.method == "GET":
        tier_user = ""
        try:
            tier_user = request.user.profile.tier
        except UserProfile.DoesNotExist:
            pass

        return JsonResponse({
            "tier_global": config.tier_padrao,
            "tier_user": tier_user,
            "tier_efetivo": tier_user or config.tier_padrao,
            "opcoes": [{"value": v, "label": l} for v, l in TIER_CHOICES],
        })

    if request.method == "POST":
        if not request.user.is_superuser:
            return JsonResponse({"error": "Apenas administradores."}, status=403)

        body = json.loads(request.body) if request.body else {}

        # Atualizar tier global
        if "tier_global" in body:
            tier_val = body["tier_global"]
            valid = [c[0] for c in TIER_CHOICES]
            if tier_val not in valid:
                return JsonResponse({"error": f"Tier invalido. Validos: {valid}"}, status=400)
            config.tier_padrao = tier_val
            config.save()

        # Atualizar tier de um usuário específico
        if "user_id" in body and "tier_user" in body:
            from django.contrib.auth.models import User
            try:
                target = User.objects.get(pk=body["user_id"])
                profile = target.profile
                profile.tier = body["tier_user"]
                profile.save(update_fields=["tier"])
            except User.DoesNotExist:
                return JsonResponse({"error": "Usuario nao encontrado."}, status=404)

        return JsonResponse({"ok": True, "tier_global": config.tier_padrao})

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ── Onboarding Tour ─────────────────────────────────────────────────────────


@login_required
def api_onboarding_status(request):
    """Retorna se o usuário já viu o tour."""
    try:
        seen = request.user.profile.has_seen_tour
    except UserProfile.DoesNotExist:
        seen = False
    return JsonResponse({"has_seen_tour": seen})


@login_required
@require_POST
def api_onboarding_dismiss(request):
    """Marca o tour como visto."""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)
    profile.has_seen_tour = True
    profile.save(update_fields=["has_seen_tour"])
    return JsonResponse({"ok": True})
