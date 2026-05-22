from django.contrib import admin
from .models import (
    Pasta, Processo, Documento, Admissibilidade,
    AnaliseTese, Parecer, ConfiguracaoParecer, AuditLog, CacheTese,
)


@admin.register(Pasta)
class PastaAdmin(admin.ModelAdmin):
    list_display = ("nome", "user", "posicao", "created_at")
    list_filter = ("user",)


class DocumentoInline(admin.TabularInline):
    model = Documento
    extra = 0


class AdmissibilidadeInline(admin.StackedInline):
    model = Admissibilidade
    extra = 0


class ParecerInline(admin.StackedInline):
    model = Parecer
    extra = 0


@admin.register(Processo)
class ProcessoAdmin(admin.ModelAdmin):
    list_display = ("pa", "sgpe", "recorrente", "fase", "resultado_final", "user", "created_at")
    list_filter = ("fase", "resultado_final")
    search_fields = ("pa", "sgpe", "recorrente", "user__username")
    inlines = [DocumentoInline, AdmissibilidadeInline, ParecerInline]
    readonly_fields = ("created_at", "updated_at")


@admin.register(AnaliseTese)
class AnaliseTeseAdmin(admin.ModelAdmin):
    list_display = ("processo", "ordem", "titulo", "acolhida")
    list_filter = ("acolhida",)
    search_fields = ("titulo", "processo__pa")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "categoria", "user", "processo", "fase", "provider")
    list_filter = ("categoria", "provider")
    search_fields = ("processo__pa", "user__username")
    readonly_fields = ("timestamp",)
    date_hierarchy = "timestamp"


@admin.register(ConfiguracaoParecer)
class ConfiguracaoParecerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not ConfiguracaoParecer.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CacheTese)
class CacheTeseAdmin(admin.ModelAdmin):
    list_display = ("cache_key", "tipo_infracao", "hit_count", "updated_at")
    search_fields = ("cache_key",)
    readonly_fields = ("created_at", "updated_at")
