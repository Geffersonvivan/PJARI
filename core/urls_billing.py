from django.urls import path
from .billing import checkout_view, stripe_webhook, planos_view
from .views_auth import landing_view, auth_sync_view, logout_view

urlpatterns = [
    path("", landing_view, name="landing"),
    path("auth-sync/", auth_sync_view, name="auth_sync"),
    path("logout/", logout_view, name="logout"),
    path("planos/", planos_view, name="planos"),
    path("checkout/", checkout_view, name="checkout"),
    path("webhooks/stripe/", stripe_webhook, name="stripe_webhook"),
]
