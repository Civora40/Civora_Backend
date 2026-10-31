"""
Environment variable loading and strict startup validation for Civora Backend.
Ensures invalid or missing environment configurations fail safely with a clear error.
"""
import os
from pathlib import Path
from typing import List
from decouple import Config, RepositoryEnv, Csv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"

if ENV_FILE.exists():
    env_config = Config(RepositoryEnv(str(ENV_FILE)))
else:
    from decouple import config as env_config  # type: ignore


VALID_ENVIRONMENTS = ("development", "staging", "production", "test")


def get_env(key: str, default=None, cast=None, required: bool = False):
    """Retrieve and validate an environment variable."""
    val = os.environ.get(key)
    if val is None:
        try:
            if cast is not None:
                val = env_config(key, default=default, cast=cast)
            else:
                val = env_config(key, default=default)
        except Exception as exc:
            if required:
                raise ImproperlyConfigured(
                    f"[Civora Config Error] Missing required environment variable: '{key}'"
                ) from exc
            return default
    else:
        if cast is bool:
            val = str(val).lower() in ("true", "1", "yes", "on")
        elif cast is int:
            try:
                val = int(val)
            except ValueError as exc:
                raise ImproperlyConfigured(
                    f"[Civora Config Error] Environment variable '{key}' must be an integer, got '{val}'."
                ) from exc
        elif cast is Csv:
            val = [item.strip() for item in str(val).split(",") if item.strip()]
        elif callable(cast):
            val = cast(val)

    if required and (val is None or str(val).strip() == ""):
        raise ImproperlyConfigured(
            f"[Civora Config Error] Environment variable '{key}' cannot be empty."
        )
    return val


def validate_environment(env_name: str, secret_key: str, database_url: str = "") -> None:
    """Validate critical startup invariants."""
    if env_name not in VALID_ENVIRONMENTS:
        raise ImproperlyConfigured(
            f"[Civora Config Error] Invalid CIVORA_ENV '{env_name}'. "
            f"Allowed values: {', '.join(VALID_ENVIRONMENTS)}"
        )

    if env_name in ("staging", "production"):
        if not secret_key or secret_key == "django-insecure-dev-key-change-me" or len(secret_key) < 32:
            raise ImproperlyConfigured(
                "[Civora Config Error] Production/Staging requires a strong SECRET_KEY (>= 32 chars)."
            )
        if not database_url or not database_url.startswith(("postgres://", "postgresql://")):
            raise ImproperlyConfigured(
                "[Civora Config Error] Production/Staging requires a valid PostgreSQL DATABASE_URL."
            )

