"""
Local development settings for Civora Backend.
Uses PostgreSQL if DATABASE_URL is explicitly configured; falls back to local SQLite
when USE_SQLITE_FALLBACK=True so local setup & migrations work out-of-the-box.
"""
from .base import *  # noqa: F403, F401
from .env import BASE_DIR, get_env, validate_environment

CIVORA_ENV = "development"
DEBUG = True

USE_SQLITE_FALLBACK = get_env("USE_SQLITE_FALLBACK", default=True, cast=bool)
RAW_DB_URL = get_env("DATABASE_URL", default="")

if not RAW_DB_URL and USE_SQLITE_FALLBACK:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

validate_environment(CIVORA_ENV, SECRET_KEY)

