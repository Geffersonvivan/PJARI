from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile, TierConfig, Subscription


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name = "Perfil / Créditos"
    fields = ("credits", "creditos_infinitos", "is_pro", "tier", "clerk_id")


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "clerk_id", "is_pro", "credits", "creditos_infinitos", "tier", "subscription_status")
    list_filter = ("is_pro", "creditos_infinitos", "subscription_status", "tier")
    search_fields = ("user__username", "user__email", "clerk_id")
    list_editable = ("is_pro", "credits", "creditos_infinitos", "tier")


@admin.register(TierConfig)
class TierConfigAdmin(admin.ModelAdmin):
    list_display = ("tier_padrao",)

    def has_add_permission(self, request):
        return not TierConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plano", "creditos_base", "creditos_bonus", "data_inicio", "data_expiracao", "is_active")
    list_filter = ("plano", "is_active")
    search_fields = ("user__username", "user__email")
