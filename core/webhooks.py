import json
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def clerk_webhook(request):
    """Endpoint para receber webhooks do Clerk e sincronizar usuários."""
    secret = settings.CLERK_WEBHOOK_SECRET
    if not secret:
        return HttpResponseForbidden("Webhook secret not configured")

    # Verificar assinatura Svix
    try:
        from svix.webhooks import Webhook, WebhookVerificationError

        headers = {
            "svix-id": request.headers.get("svix-id"),
            "svix-timestamp": request.headers.get("svix-timestamp"),
            "svix-signature": request.headers.get("svix-signature"),
        }
        wh = Webhook(secret)
        evt = wh.verify(request.body.decode("utf-8"), headers)
    except WebhookVerificationError:
        return HttpResponseForbidden("Invalid signature")
    except ImportError:
        # svix não instalado — aceitar sem verificação em dev
        evt = json.loads(request.body)

    event_type = evt.get("type")
    data = evt.get("data", {})

    if event_type in ("user.created", "user.updated"):
        clerk_id = data.get("id")
        if not clerk_id:
            return HttpResponse("Missing user ID", status=400)

        emails = data.get("email_addresses", [])
        email = emails[0].get("email_address", "") if emails else ""

        user, created = User.objects.update_or_create(
            username=clerk_id,
            defaults={
                "first_name": (data.get("first_name") or "")[:30],
                "last_name": (data.get("last_name") or "")[:30],
                "email": email,
                "is_active": True,
            },
        )
        if created:
            user.set_unusable_password()
            user.save()

        # Sincronizar clerk_id no UserProfile
        from core.models import UserProfile
        UserProfile.objects.filter(user=user).update(clerk_id=clerk_id)

        logger.info("Clerk sync: %s user %s (%s)", "created" if created else "updated", clerk_id, email)

    elif event_type == "user.deleted":
        clerk_id = data.get("id")
        if clerk_id:
            User.objects.filter(username=clerk_id).delete()
            logger.info("Clerk sync: deleted user %s", clerk_id)

    return HttpResponse("OK", status=200)
