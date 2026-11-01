"""
Custom User model and identity records for Civora Backend.
Uses email as the primary login identifier and supports verified phone/WhatsApp mapping.
"""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from common.models import TimestampedModel
from common.validators import validate_e164_phone


class UserManager(BaseUserManager):
    """Custom manager for email-based User authentication."""

    def create_user(self, email: str, password: str = None, **extra_fields):
        if not email:
            raise ValueError("Users must have a valid email address.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_email_verified", True)
        extra_fields.setdefault("is_platform_admin", True)
        return self.create_user(email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, TimestampedModel):
    """
    Canonical Civora User identity.
    Scoped to organizations via OrganizationMembership and projects via ProjectMembership.
    """

    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=255)
    first_name = models.CharField(max_length=120, blank=True, default="")
    last_name = models.CharField(max_length=120, blank=True, default="")
    display_name = models.CharField(max_length=255, blank=True, default="")
    job_title = models.CharField(max_length=150, blank=True, default="")
    department = models.CharField(max_length=150, blank=True, default="")
    professional_registration = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Engineering registration reference (e.g., COREN #CR-89412).",
    )
    coren_status = models.CharField(
        max_length=120,
        default="unregistered",
        help_text="verified | pending | unregistered",
    )
    phone_number = models.CharField(
        max_length=24,
        blank=True,
        null=True,
        unique=True,
        validators=[validate_e164_phone],
        help_text="Verified E.164 phone number for WhatsApp/external channel attribution.",
    )
    time_zone = models.CharField(max_length=64, default="Africa/Lagos")
    account_status = models.CharField(
        max_length=32,
        default="active",
        db_index=True,
        help_text="active | suspended | pending",
    )
    onboarding_step = models.PositiveSmallIntegerField(default=1)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    profile_metadata = models.JSONField(default=dict, blank=True)
    is_email_verified = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)
    is_platform_admin = models.BooleanField(
        default=False,
        help_text="Platform-level administration flag, subject to explicit RBAC scope.",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "civora_users"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"], name="idx_user_email"),
            models.Index(fields=["phone_number"], name="idx_user_phone"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}>"

