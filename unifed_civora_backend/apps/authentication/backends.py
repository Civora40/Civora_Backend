"""
Custom JWT authentication backend enforcing Civora Phase 3 session revocation
and account suspension boundaries on every authenticated API request.
"""
from datetime import timedelta
from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from .models import Session


class CivoraJWTAuthentication(JWTAuthentication):
    """
    Extends SimpleJWT authentication to verify:
    1. The authenticated user account is active and not suspended.
    2. If a tracked `Session` record exists for the token's `jti`, it has not been
       explicitly revoked or expired.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result

        if not user.is_active or getattr(user, "account_status", "active") == "suspended":
            raise AuthenticationFailed("This account is currently suspended or inactive.")

        jti = str(validated_token.get("jti", ""))
        if jti:
            session = Session.objects.filter(jti=jti).first()
            if session is not None:
                now = timezone.now()
                if session.is_revoked or session.expires_at <= now:
                    raise AuthenticationFailed("Session has been revoked or expired. Please sign in again.")
                request.auth_session = session
                if not session.last_active_at or (now - session.last_active_at) > timedelta(minutes=5):
                    session.last_active_at = now
                    session.save(update_fields=["last_active_at", "updated_at"])

        return user, validated_token


class CivoraJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.authentication.backends.CivoraJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }

