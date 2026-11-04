"""
Authentication identity models (Phase 2 — Database Architecture and Data Modelling):
Account and Session entities linked to User and Organization.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from common.models import TimestampedModel


class AuthProvider(models.TextChoices):
    CREDENTIALS = "credentials", "Email & Password Credentials"
    GOOGLE = "google", "Google Workspace OAuth"
    OIDC = "oidc", "Enterprise OIDC / SSO"
    WHATSAPP = "whatsapp", "WhatsApp Verified Identity"
    TELEGRAM = "telegram", "Telegram Bot Identity"


class ClientSurface(models.TextChoices):
    WEB_PE = "web_pe", "PE & Reviewer Web Portal"
    WEB_STAKEHOLDER = "web_stakeholder", "Stakeholder Oversight Portal"
    WEB_ADMIN = "web_admin", "Platform Admin Console"
    MOBILE_APP = "mobile_app", "Civora Mobile App"
    API_CLIENT = "api_client", "API / Service Client"


class Account(TimestampedModel):
    """
    External or credential authentication account linked to a User (Phase 2).
    Supports password credentials, SSO providers, and verified messaging channel identities.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_accounts",
        db_index=True,
    )
    provider = models.CharField(
        max_length=32,
        choices=AuthProvider.choices,
        default=AuthProvider.CREDENTIALS,
        db_index=True,
    )
    provider_account_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Normalized email, OAuth subject ID, or E.164 phone/channel handle.",
    )
    is_verified = models.BooleanField(default=True, db_index=True)
    last_used_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "civora_auth_accounts"
        ordering = ["-last_used_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_account_id"],
                name="uq_auth_provider_account_id",
            )
        ]
        indexes = [
            models.Index(fields=["user", "provider"], name="idx_account_user_provider"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} [{self.provider}:{self.provider_account_id}]"


class Session(TimestampedModel):
    """
    Tracked authentication session for web portals, admin console, and mobile clients (Phase 2).
    Enables explicit session auditing, device tracking, and immediate session revocation.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_sessions",
        db_index=True,
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auth_sessions",
        db_index=True,
    )
    jti = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text="Unique JWT ID (jti) or session token identifier.",
    )
    client_surface = models.CharField(
        max_length=32,
        choices=ClientSurface.choices,
        default=ClientSurface.WEB_PE,
        db_index=True,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")
    is_revoked = models.BooleanField(default=False, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    last_active_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "civora_auth_sessions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_revoked", "expires_at"], name="idx_session_user_active"),
        ]

    def revoke(self) -> None:
        self.is_revoked = True
        self.revoked_at = timezone.now()
        self.save(update_fields=["is_revoked", "revoked_at", "updated_at"])

    def __str__(self) -> str:
        status_label = "revoked" if self.is_revoked else "active"
        return f"Session {self.jti[:12]} ({self.user.email} - {status_label})"
