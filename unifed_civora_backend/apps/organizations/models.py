"""
Organization and OrganizationMembership models (Phase 2 & 4).
Every tenant-owned record in Civora is scoped to an Organization.
"""
from django.conf import settings
from django.db import models
from common.models import TimestampedModel


class Organization(TimestampedModel):
    """Multi-tenant organization boundary."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120, unique=True, db_index=True)
    code = models.CharField(max_length=40, unique=True, db_index=True)
    tier = models.CharField(
        max_length=40,
        default="enterprise",
        help_text="enterprise | contractor | consultancy | government_agency",
    )
    status = models.CharField(
        max_length=32,
        default="active",
        db_index=True,
        help_text="active | inactive | provisioning | suspended",
    )
    cac_number = models.CharField(max_length=80, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=40, blank=True, default="")
    office_address = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    country = models.CharField(max_length=100, default="Nigeria")
    compliance_threshold_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=80.00,
        help_text="Configurable compliance threshold percentage (default 80%).",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "civora_organizations"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class OrganizationMembership(TimestampedModel):
    """Links a User to an Organization with an explicit RBAC Role."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
        db_index=True,
    )
    role = models.ForeignKey(
        "access_control.Role",
        on_delete=models.PROTECT,
        related_name="organization_memberships",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_organization_invitations",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "civora_organization_memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="uq_organization_user_membership",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"], name="idx_org_member_active"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} @ {self.organization.code} ({self.role.slug})"


class OrganizationInvitation(TimestampedModel):
    """
    Explicit organization/project invitation token for controlled onboarding (Phase 3 & 4).
    Prevents self-assigned privileged roles.
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invitations",
        db_index=True,
    )
    email = models.EmailField(db_index=True)
    role = models.ForeignKey(
        "access_control.Role",
        on_delete=models.PROTECT,
        related_name="invitations",
    )
    invitation_code = models.CharField(max_length=40, unique=True, db_index=True)
    status = models.CharField(
        max_length=24,
        default="pending",
        db_index=True,
        help_text="pending | accepted | revoked | expired",
    )
    assigned_project_codes = models.JSONField(default=list, blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_invitations",
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_organization_invitations"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Invite {self.invitation_code} -> {self.email} ({self.status})"

