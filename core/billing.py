"""
Billing views — Stripe checkout, webhook, planos page.
"""

import datetime
import logging

import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .models import Subscription, UserProfile

_log = logging.getLogger(__name__)

# ── Planos ───────────────────────────────────────────────────────────────────

PLANS = {
    "basic": {
        "title": "Pacote Inicial",
        "description": "5 pareceres avulsos",
        "price": 49.90,
        "credits": 5,
        "credits_base": 5,
        "credits_bonus": 0,
        "is_subscription": False,
    },
    "pro": {
        "title": "Plano Profissional",
        "description": "30 pareceres/mês + suporte prioritário",
        "price": 199.90,
        "credits": 30,
        "credits_base": 25,
        "credits_bonus": 5,
        "is_subscription": True,
    },
}


# ── Checkout ─────────────────────────────────────────────────────────────────


@login_required
@require_GET
def checkout_view(request):
    """Cria sessão Stripe Checkout e redireciona."""
    stripe.api_key = settings.STRIPE_SECRET_KEY

    plan_key = request.GET.get("plan", "pro")
    plan = PLANS.get(plan_key, PLANS["pro"])

    payer_email = request.user.email or f"user_{request.user.id}@pjari.com.br"

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            customer_email=payer_email,
            client_reference_id=str(request.user.id),
            metadata={"plan_key": plan_key},
            line_items=[
                {
                    "price_data": {
                        "currency": "brl",
                        "product_data": {
                            "name": plan["title"],
                            "description": plan["description"],
                        },
                        "unit_amount": int(plan["price"] * 100),
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=request.build_absolute_uri("/planos/?success=1"),
            cancel_url=request.build_absolute_uri("/planos/?cancel=1"),
        )
        return redirect(session.url)
    except Exception:
        _log.exception("Erro ao criar sessão Stripe Checkout")
        return HttpResponse("Erro ao gerar checkout. Tente novamente.", status=500)


# ── Webhook ──────────────────────────────────────────────────────────────────


@csrf_exempt
def stripe_webhook(request):
    """Recebe eventos da Stripe (checkout.session.completed)."""
    if request.method != "POST":
        return HttpResponse(status=405)

    stripe.api_key = settings.STRIPE_SECRET_KEY
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

    if not endpoint_secret:
        _log.error("STRIPE_WEBHOOK_SECRET não configurado")
        return HttpResponse(status=400)

    sig = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(request.body, sig, endpoint_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    event_type = event.type if hasattr(event, "type") else event.get("type")

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event.data.object if hasattr(event.data, "object") else event["data"]["object"]

        if session.get("payment_status") == "paid":
            _process_payment(session)

    return HttpResponse(status=200)


def _process_payment(session):
    """Processa pagamento confirmado."""
    user_id = session.get("client_reference_id")
    if not user_id:
        _log.warning("Webhook sem client_reference_id: %s", session.get("id"))
        return

    # Identificar plano via metadata ou amount
    plan_key = (session.get("metadata") or {}).get("plan_key")
    plan = PLANS.get(plan_key) if plan_key else None

    if not plan:
        amount_cents = int(session.get("amount_total", 0))
        plan_key, plan = next(
            ((k, v) for k, v in PLANS.items() if int(v["price"] * 100) == amount_cents),
            (None, None),
        )

    if not plan:
        _log.error("Plano não identificado para sessão %s", session.get("id"))
        return

    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(id=user_id)
            payment_id = session.get("payment_intent") or session.get("id")

            if plan["is_subscription"]:
                agora = timezone.now()
                expiracao = agora + datetime.timedelta(days=30)

                user.subscriptions.filter(is_active=True).update(is_active=False)

                Subscription.objects.create(
                    user=user,
                    plano=plan_key,
                    creditos_base=plan["credits_base"],
                    creditos_bonus=plan["credits_bonus"],
                    data_inicio=agora,
                    data_expiracao=expiracao,
                    stripe_session_id=payment_id,
                    is_active=True,
                )

                user.profile.credits = plan["credits"]
                user.profile.is_pro = True
                user.profile.subscription_status = "active"
                user.profile.subscription_start_at = agora
                user.profile.subscription_expires_at = expiracao
                user.profile.save(update_fields=[
                    "credits", "is_pro", "subscription_status",
                    "subscription_start_at", "subscription_expires_at",
                ])
            else:
                user.profile.credits += plan["credits"]
                user.profile.save(update_fields=["credits"])

            _log.info(
                "Pagamento processado: user=%s plano=%s valor=R$%.2f",
                user.username, plan_key, plan["price"],
            )
    except User.DoesNotExist:
        _log.error("Usuário %s não encontrado no webhook", user_id)
    except Exception:
        _log.exception("Erro ao processar pagamento webhook")


# ── Planos page ──────────────────────────────────────────────────────────────


def planos_view(request):
    """Página de planos e preços."""
    user_credits = None
    user_is_pro = False

    if request.user.is_authenticated:
        try:
            user_credits = request.user.profile.credits
            user_is_pro = request.user.profile.is_pro
        except UserProfile.DoesNotExist:
            pass

    return render(request, "core/planos.html", {
        "plans": PLANS,
        "success": request.GET.get("success") == "1",
        "cancel": request.GET.get("cancel") == "1",
        "user_credits": user_credits,
        "user_is_pro": user_is_pro,
    })
