from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


TIER_CHOICES = [
    ("flash", "Flash (Econômico)"),
    ("pro", "Pro (Avançado)"),
    ("max", "Máximo (Todas as fases)"),
]


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    clerk_id = models.CharField(max_length=100, unique=True, null=True, blank=True, db_index=True)

    # Plano e créditos
    is_pro = models.BooleanField(default=False)
    credits = models.IntegerField(default=5)
    subscription_status = models.CharField(max_length=50, default="inactive")
    subscription_start_at = models.DateTimeField(null=True, blank=True)
    subscription_expires_at = models.DateTimeField(null=True, blank=True)

    # UX
    has_seen_tour = models.BooleanField(default=False)
    can_view_global_stats = models.BooleanField(
        default=False, verbose_name="Ver Painel Global"
    )

    # IA tier override
    tier = models.CharField(
        max_length=20,
        choices=[("", "Usar padrão global")] + TIER_CHOICES,
        default="",
        blank=True,
        verbose_name="Nível de IA",
    )

    class Meta:
        verbose_name = "Perfil de Usuário"
        verbose_name_plural = "Perfis de Usuários"

    def __str__(self):
        return f"{self.user.username} — PRO: {self.is_pro}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


class TierConfig(models.Model):
    """Configuração global do nível de IA. Singleton."""

    tier_padrao = models.CharField(
        max_length=20,
        choices=TIER_CHOICES,
        default="flash",
        verbose_name="Nível padrão de IA",
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        from django.core.cache import cache
        cache.delete("tier_config")

    @classmethod
    def load(cls):
        from django.core.cache import cache
        config = cache.get("tier_config")
        if config is None:
            config, _ = cls.objects.get_or_create(pk=1)
            cache.set("tier_config", config, 3600)
        return config

    class Meta:
        verbose_name = "Nível de IA (Global)"
        verbose_name_plural = "Nível de IA (Global)"

    def __str__(self):
        return f"Nível de IA: {self.get_tier_padrao_display()}"


class Subscription(models.Model):
    PLANO_CHOICES = [
        ("basic", "Básico"),
        ("pro", "Profissional"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="subscriptions")
    plano = models.CharField(max_length=20, choices=PLANO_CHOICES)
    creditos_base = models.IntegerField()
    creditos_bonus = models.IntegerField(default=0)
    data_inicio = models.DateTimeField()
    data_expiracao = models.DateTimeField()
    stripe_session_id = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True, db_index=True)

    @property
    def creditos_total(self):
        return self.creditos_base + self.creditos_bonus

    class Meta:
        ordering = ["-data_inicio"]
        verbose_name = "Assinatura"
        verbose_name_plural = "Assinaturas"

    def __str__(self):
        return (
            f"{self.user.username} — {self.get_plano_display()} "
            f"({self.data_inicio:%d/%m/%Y} -> {self.data_expiracao:%d/%m/%Y})"
        )


class TermoAceite(models.Model):
    """Registro de aceite dos termos de uso pelo usuário."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="termos_aceitos")
    versao = models.CharField(max_length=20, default="1.0")
    aceito_em = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-aceito_em"]
        verbose_name = "Aceite de Termos"
        verbose_name_plural = "Aceites de Termos"
        unique_together = [("user", "versao")]

    def __str__(self):
        return f"{self.user.username} — v{self.versao} em {self.aceito_em:%d/%m/%Y}"

    @classmethod
    def usuario_aceitou(cls, user, versao="1.0"):
        """Verifica se o usuário aceitou a versão vigente."""
        return cls.objects.filter(user=user, versao=versao).exists()
