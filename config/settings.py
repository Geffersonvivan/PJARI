"""
Django settings for P-JARI NEW.
"""

from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# ── Apps ─────────────────────────────────────────────────────────────────────

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local
    "core",
    "pareceres",
]

# Silk (opt-in)
if os.environ.get("SILK_ENABLED") == "True":
    INSTALLED_APPS.append("silk")

# ── Middleware ────────────────────────────────────────────────────────────────

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Clerk JWT — roda após AuthenticationMiddleware
    "core.middleware.ClerkAuthMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Verifica créditos antes de criar processos
    "core.middleware_credits.CreditCheckMiddleware",
    # Exige aceite dos termos de uso
    "core.middleware_termos.RequireTermosMiddleware",
]

if os.environ.get("SILK_ENABLED") == "True":
    MIDDLEWARE.insert(3, "silk.middleware.SilkyMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.clerk_keys",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ── Database ─────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Postgres em produção (Railway / managed)
    import re
    m = re.match(
        r"postgres(?:ql)?://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<name>.+)",
        DATABASE_URL,
    )
    if m:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": m.group("name"),
                "USER": m.group("user"),
                "PASSWORD": m.group("password"),
                "HOST": m.group("host"),
                "PORT": m.group("port") or "5432",
            }
        }
    else:
        raise ValueError(f"DATABASE_URL inválido: {DATABASE_URL}")
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ── Auth ─────────────────────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LOGIN_URL = "/"
LOGIN_REDIRECT_URL = "/app/"

# ── i18n ─────────────────────────────────────────────────────────────────────

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# ── Static / Media ───────────────────────────────────────────────────────────

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# GCS para uploads em produção
USE_GCS = os.environ.get("USE_GCS", "False") == "True"

if USE_GCS:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        "OPTIONS": {
            "bucket_name": os.environ.get("GCS_BUCKET_NAME", "pjari-midias"),
            "default_acl": None,
            "querystring_auth": True,
            "expiration": 86400,  # 24h signed URLs
        },
    }
    GCS_CREDENTIALS_FILE = os.environ.get("GCS_CREDENTIALS_FILE")
    if GCS_CREDENTIALS_FILE:
        from google.oauth2 import service_account
        GS_CREDENTIALS = service_account.Credentials.from_service_account_file(
            GCS_CREDENTIALS_FILE
        )
else:
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"
    STORAGES["default"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    }

# ── Upload limits ──────────────────────────────────────────────────────────

DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024   # 100 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024   # 100 MB

# ── Redis / Cache ────────────────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

# ── Celery ───────────────────────────────────────────────────────────────────

CELERY_BROKER_URL = REDIS_URL or "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = REDIS_URL or "redis://localhost:6379/0"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

# Duas filas: fast (fases 1-4) e heavy (fase 5 — parecer)
CELERY_TASK_ROUTES = {
    "pareceres.tasks.gerar_parecer_task": {"queue": "heavy"},
}
CELERY_TASK_DEFAULT_QUEUE = "fast"

# ── Clerk ────────────────────────────────────────────────────────────────────

CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
CLERK_WEBHOOK_SECRET = os.environ.get("CLERK_WEBHOOK_SECRET", "")
CLERK_JWKS_URL = os.environ.get("CLERK_JWKS_URL", "")

# ── LLM API Keys ────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
VERTEX_PROJECT_ID = os.environ.get("VERTEX_PROJECT_ID", "")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
VERTEX_RAG_DATASTORE_ID = os.environ.get("VERTEX_RAG_DATASTORE_ID", "")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Stripe ───────────────────────────────────────────────────────────────────

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# ── Sentry ───────────────────────────────────────────────────────────────────

SENTRY_DSN = os.environ.get("SENTRY_DSN")

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    import logging as _logging
    from sentry_sdk.integrations.logging import LoggingIntegration

    def _sentry_before_send(event, hint):
        if "exc_info" in hint:
            exc_type = hint["exc_info"][0]
            try:
                from django.http import Http404
                from django.core.exceptions import PermissionDenied
                if exc_type in (Http404, PermissionDenied):
                    return None
            except ImportError:
                pass
        return event

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment="development" if DEBUG else "production",
        integrations=[
            DjangoIntegration(transaction_style="url"),
            CeleryIntegration(monitor_beat_tasks=True, propagate_traces=True),
            RedisIntegration(),
            LoggingIntegration(level=_logging.WARNING, event_level=_logging.ERROR),
        ],
        traces_sample_rate=0.0 if DEBUG else 0.2,
        send_default_pii=False,
        before_send=_sentry_before_send,
    )

# ── Security (prod) ─────────────────────────────────────────────────────────

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# ── Email ────────────────────────────────────────────────────────────────────

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "P-JARI <noreply@pjari.sc.gov.br>")

# ── Misc ─────────────────────────────────────────────────────────────────────

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
