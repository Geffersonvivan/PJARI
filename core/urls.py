from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("termos/", views.termos_visualizar, name="termos_visualizar"),
    path("termos/aceitar/", views.termos_aceitar, name="termos_aceitar"),
    path("api/tier-config/", views.api_tier_config, name="api_tier_config"),
    path("api/onboarding/status/", views.api_onboarding_status, name="api_onboarding_status"),
    path("api/onboarding/dismiss/", views.api_onboarding_dismiss, name="api_onboarding_dismiss"),
]
