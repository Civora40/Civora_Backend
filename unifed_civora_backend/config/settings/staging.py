"""
Staging environment settings for Civora Backend.
"""
from .base import *  # noqa: F403, F401
from .env import validate_environment

CIVORA_ENV = "staging"
DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

validate_environment(CIVORA_ENV, SECRET_KEY, DATABASE_URL)

