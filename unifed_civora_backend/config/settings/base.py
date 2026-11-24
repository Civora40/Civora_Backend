"""
Base Django settings for Civora Backend.
Shared across development, staging, production, and test environments.
"""
from datetime import timedelta
from pathlib import Path
import dj_database_url
from decouple import Csv
from .env import BASE_DIR, get_env, validate_environment

CIVORA_ENV = get_env("CIVORA_ENV", default="development")
SECRET_KEY = get_env(
    "SECRET_KEY",
    default="django-insecure-civora-dev-secret-key-change-in-production-2026",
    required=True,
)
DEBUG = get_env("DEBUG", default=(CIVORA_ENV == "development"), cast=bool)

ALLOWED_HOSTS = get_env(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1,0.0.0.0,testserver,.onrender.com,.amijad-civora.com,api.amijad-civora.com",
    cast=Csv(),
)
for _required_host in (".onrender.com", ".amijad-civora.com", "api.amijad-civora.com"):
    if _required_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_required_host)
RENDER_EXTERNAL_HOSTNAME = get_env("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# ==============================================================================
# APPLICATION DEFINITION
# ==============================================================================
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
]

LOCAL_APPS = [
    "common.apps.CommonConfig",
    "apps.users.apps.UsersConfig",
    "apps.organizations.apps.OrganizationsConfig",
    "apps.access_control.apps.AccessControlConfig",
    "apps.authentication.apps.AuthenticationConfig",
    "apps.projects.apps.ProjectsConfig",
    "apps.submissions.apps.SubmissionsConfig",
    "apps.evidence.apps.EvidenceConfig",
    "apps.integrations.apps.IntegrationsConfig",
    "apps.tasks.apps.TasksConfig",
    "apps.performance.apps.PerformanceConfig",
    "apps.reports.apps.ReportsConfig",
    "apps.document_management.apps.DocumentManagementConfig",
    "apps.ai_assistance.apps.AiAssistanceConfig",
    "apps.approvals.apps.ApprovalsConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.audit.apps.AuditConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "common.middleware.tenant.TenantContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ==============================================================================
# DATABASE (PostgreSQL Primary)
# ==============================================================================
DATABASE_URL = get_env(
    "DATABASE_URL",
    default="postgres://postgres:postgres@localhost:5432/civora_db",
)

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=get_env("DB_CONN_MAX_AGE", default=60, cast=int),
        conn_health_checks=True,
    )
}
CSRF_TRUSTED_ORIGINS = get_env(
    "CSRF_TRUSTED_ORIGINS",
    default="https://*.onrender.com,https://*.amijad-civora.com,https://amijad-civora.com,https://*.vercel.app,http://localhost:3000,http://localhost:3001",
    cast=Csv(),
)
for _trusted_origin in (
    "https://*.onrender.com",
    "https://*.amijad-civora.com",
    "https://amijad-civora.com",
    "https://*.vercel.app",
):
    if _trusted_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_trusted_origin)

# ==============================================================================
# AUTHENTICATION & PASSWORD VALIDATION
# ==============================================================================
AUTH_USER_MODEL = "users.User"

# Dedicated Platform Administrator Credentials (configurable via .env)
CIVORA_ADMIN_EMAIL = get_env("CIVORA_ADMIN_EMAIL", default="Civora40@gmail.com")
CIVORA_ADMIN_PASSWORD = get_env("CIVORA_ADMIN_PASSWORD", default="v;H#bRZby'Vc#0mT")
CIVORA_ADMIN_NAME = get_env("CIVORA_ADMIN_NAME", default="Civora Platform Administrator")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ==============================================================================
#DJANGO REST FRAMEWORK & JWT & OPENAPI
# ==============================================================================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.authentication.backends.CivoraJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "common.exceptions.civora_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=get_env("JWT_ACCESS_MINUTES", default=60, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=get_env("JWT_REFRESH_DAYS", default=7, cast=int)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Civora Centralized Reporting Hub API",
    "DESCRIPTION": (
        "Centralized REST API for Civora Mobile App, PE & Stakeholder Frontend, "
        "and Platform Admin Frontend. Authenticate via `/api/v1/auth/login/` or "
        "`/api/v1/auth/admin/login/`, copy the `access` JWT token, and click **Authorize**."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": False,
        "docExpansion": "list",
        "filter": True,
    },
    "TAGS": [
        {"name": "System & Health", "description": "API root index, health check, and database readiness"},
        {"name": "Authentication", "description": "Login, Dedicated Admin Login, Registration, JWT Refresh, Logout & Password Reset"},
        {"name": "Users & Onboarding", "description": "User profiles, 10-step COREN/CAC onboarding flow, and admin user status controls"},
        {"name": "Access Control (RBAC)", "description": "Canonical system roles and explicit permission capabilities"},
        {"name": "Organizations & Invitations", "description": "Multi-tenant organizations, memberships, and controlled onboarding invitations"},
        {"name": "Projects", "description": "Project registry, initiation, lifecycle state transitions, and project team assignments"},
        {"name": "Audit & Governance", "description": "Immutable append-only security and operational audit trail"},
    ],
}

# ==============================================================================
# MULTI-REPOSITORY CORS & CLIENT ORIGINS
# ==============================================================================
# Supports:
# 1. Civora-PE_and_Stakeholder_Frontend (default port 3000)
# 2. Civora-Admin_Frontend (default port 3001)
# 3. Civora_Mobile_App (Expo/Metro ports 8081, 19000, 19006)
CORS_ALLOWED_ORIGINS = get_env(
    "CORS_ALLOWED_ORIGINS",
    default="https://civora-gamma.vercel.app,http://localhost:3000,http://localhost:3001,http://localhost:8081,http://127.0.0.1:3000,http://127.0.0.1:3001",
    cast=Csv(),
)
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://.*\.vercel\.app$",
    r"^https://(.*\.)?amijad-civora\.com$",
]
CORS_ALLOW_CREDENTIALS = True

# ==============================================================================
# CELERY & REDIS CONFIGURATION
# ==============================================================================
CELERY_BROKER_URL = get_env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = get_env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"

# ==============================================================================
# INTERNATIONALIZATION & STATIC / MEDIA
# ==============================================================================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ==============================================================================
# STRUCTURED LOGGING
# ==============================================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "infrastructure.logging.formatters.StructuredJsonFormatter",
        },
        "console": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console" if DEBUG else "structured",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": get_env("LOG_LEVEL", default="INFO"),
    },
}

# ==============================================================================
# EXTERNAL CHANNEL INTEGRATIONS (Phase 6: WhatsApp & Telegram)
# ==============================================================================
WHATSAPP_ENABLED = get_env("WHATSAPP_ENABLED", default="true").lower() in ("true", "1", "yes")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = get_env(
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN", default="civora_wa_verify_token_dev_2026"
)
WHATSAPP_APP_SECRET = get_env(
    "WHATSAPP_APP_SECRET", default="civora_wa_app_secret_dev_2026"
)
WHATSAPP_ACCESS_TOKEN = get_env("WHATSAPP_ACCESS_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = get_env("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_BUSINESS_ACCOUNT_ID = get_env("WHATSAPP_BUSINESS_ACCOUNT_ID", default="")

TELEGRAM_ENABLED = get_env("TELEGRAM_ENABLED", default="true").lower() in ("true", "1", "yes")
TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_WEBHOOK_SECRET_TOKEN = get_env(
    "TELEGRAM_WEBHOOK_SECRET_TOKEN", default="civora_tg_secret_token_dev_2026"
)

# Telegram MTProto / Client App API Credentials
TELEGRAM_APP_API_ID = int(get_env("TELEGRAM_APP_API_ID", default="30212373"))
TELEGRAM_APP_API_HASH = get_env(
    "TELEGRAM_APP_API_HASH", default="89fffb75ebbd496fef7da246bfbb849e"
)
TELEGRAM_DC_PROD_IP = get_env("TELEGRAM_DC_PROD_IP", default="149.154.167.50")
TELEGRAM_DC_PROD_PORT = int(get_env("TELEGRAM_DC_PROD_PORT", default="443"))
TELEGRAM_DC_TEST_IP = get_env("TELEGRAM_DC_TEST_IP", default="149.154.167.40")
TELEGRAM_DC_TEST_PORT = int(get_env("TELEGRAM_DC_TEST_PORT", default="443"))
TELEGRAM_USE_TEST_DC = get_env("TELEGRAM_USE_TEST_DC", default="false").lower() in ("true", "1", "yes")

# Telegram Data Center RSA Public Keys
TELEGRAM_RSA_PUBLIC_KEY_PROD = get_env(
    "TELEGRAM_RSA_PUBLIC_KEY_PROD",
    default="""-----BEGIN RSA PUBLIC KEY-----
MIIBCgKCAQEA6LszBcC1LGzyr992NzE0ieY+BSaOW622Aa9Bd4ZHLl+TuFQ4lo4g
5nKaMBwK/BIb9xUfg0Q29/2mgIR6Zr9krM7HjuIcCzFvDtr+L0GQjae9H0pRB2OO
62cECs5HKhT5DZ98K33vmWiLowc621dQuwKWSQKjWf50XYFw42h21P2KXUGyp2y/
+aEyZ+uVgLLQbRA1dEjSDZ2iGRy12Mk5gpYc397aYp438fsJoHIgJ2lgMv5h7WY9
t6N/byY9Nw9p21Og3AoXSL2q/2IJ1WRUhebgAdGVMlV1fkuOQoEzR7EdpqtQD9Cs
5+bfo3Nhmcyvk5ftB0WkJ9z6bNZ7yxrP8wIDAQAB
-----END RSA PUBLIC KEY-----""",
)

TELEGRAM_RSA_PUBLIC_KEY_TEST = get_env(
    "TELEGRAM_RSA_PUBLIC_KEY_TEST",
    default="""-----BEGIN RSA PUBLIC KEY-----
MIIBCgKCAQEAyMEdY1aR+sCR3ZSJrtztKTKqigvO/vBfqACJLZtS7QMgCGXJ6XIR
yy7mx66W0/sOFa7/1mAZtEoIokDP3ShoqF4fVNb6XeqgQfaUHd8wJpDWHcR2OFwv
plUUI1PLTktZ9uW2WE23b+ixNwJjJGwBDJPQEQFBE+vfmH0JP503wr5INS1poWg/
j25sIWeYPHYeOrFp/eXaqhISP6G+q2IeTaWTXpwZj4LzXq5YOpk4bYEQ6mvRq7D1
aHWfYmlEGepfaYR8Q0YqvvhYtMte3ITnuSJs171+GDqpdKcSwHnd6FudwGO4pcCO
j4WcDuXc2CTHgH8gFTNhp/Y8/SpDOhvn9QIDAQAB
-----END RSA PUBLIC KEY-----""",
)



