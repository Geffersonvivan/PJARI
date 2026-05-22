from django.contrib import admin
from django.urls import path, include
from django.conf import settings

urlpatterns = [
    path("admin/", admin.site.urls),
    path("webhooks/", include("core.urls_webhooks")),
    path("health/", include("core.urls_health")),
    path("", include("core.urls")),
    path("", include("core.urls_billing")),
    path("", include("pareceres.urls")),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if "silk" in settings.INSTALLED_APPS:
    urlpatterns.append(path("silk/", include("silk.urls", namespace="silk")))

admin.site.site_header = "P-JARI Administração"
admin.site.site_title = "P-JARI"
