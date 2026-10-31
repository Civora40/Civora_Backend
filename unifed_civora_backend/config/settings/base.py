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
    default="localhost,127.0.0.1,0.0.0.0,testserver",
    cast=Csv(),
)

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

# ==============================================================================
# AUTHENTICATION & PASSWORD VALIDATION
# ==============================================================================
AUTH_USER_MODEL = "users.User"

# Dedicated Platform Administrator Credentials (configurable via .env)
CIVORA_ADMIN_EMAIL = get_env("CIVORA_ADMIN_EMAIL", default="admin@civora.internal")
CIVORA_ADMIN_PASSWORD = get_env("CIVORA_ADMIN_PASSWORD", default="Civora2026!")
CIVORA_ADMIN_NAME = get_env("CIVORA_ADMIN_NAME", default="Engr. Babatunde Fashola-Smith")

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
        "rest_framework_simplejwt.authentication.JWTAuthentication",
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
        "and Platform Admin Frontend."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
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
    default="http://localhost:3000,http://localhost:3001,http://localhost:8081,http://127.0.0.1:3000,http://127.0.0.1:3001",
    cast=Csv(),
)
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

