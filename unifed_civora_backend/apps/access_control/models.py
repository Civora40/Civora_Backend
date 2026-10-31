"""
Role, Permission, and RolePermission models for Civora RBAC (Phase 2 & 3).
Models permissions as explicit capabilities rather than relying on role names alone.
"""
from django.db import models
from common.models import TimestampedModel


class SystemRoleSlug(models.TextChoices):
    PLATFORM_ADMIN = "platform_admin", "Platform Admin"
    ORGANIZATION_ADMIN = "organization_admin", "Organization Admin"
    PROJECT_ENGINEER = "project_engineer", "Project Engineer"
    SUPERVISING_REVIEWER = "supervising_reviewer", "Supervising Technical Reviewer"
    FIELD_PERSONNEL = "field_personnel", "Field Personnel"
    READ_ONLY_STAKEHOLDER = "read_only_stakeholder", "Read-Only Stakeholder"


class Permission(TimestampedModel):
    """Explicit capability atom (e.g., 'submissions.verify', 'reports.approve', 'reports.release')."""

    codename = models.CharField(max_length=120, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    module = models.CharField(max_length=80, db_index=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "civora_permissions"
        ordering = ["module", "codename"]

    def __str__(self) -> str:
        return f"{self.codename} ({self.name})"


class Role(TimestampedModel):
    """
    Canonical or organization-custom Role grouping explicit permissions.
    """

    slug = models.SlugField(max_length=80, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    is_system_role = models.BooleanField(default=True)
    permissions = models.ManyToManyField(
        Permission,
        through="RolePermission",
        related_name="roles",
    )

    class Meta:
        db_table = "civora_roles"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} [{self.slug}]"


class RolePermission(TimestampedModel):
    """Join entity linking Roles to explicit Permission capabilities."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="permission_roles")

    class Meta:
        db_table = "civora_role_permissions"
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"],
                name="uq_role_permission",
            )
        ]

    def __str__(self) -> str:
        return f"{self.role.slug} -> {self.permission.codename}"

