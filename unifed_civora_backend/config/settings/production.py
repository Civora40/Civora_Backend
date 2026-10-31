"""
Hardened production environment settings for Civora Backend.
"""
from .base import *  # noqa: F403, F401
from .env import validate_environment

CIVORA_ENV = "production"
DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

validate_environment(CIVORA_ENV, SECRET_KEY, DATABASE_URL)

