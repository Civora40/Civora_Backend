"""
Foundational abstract base models for Civora Backend.
Enforces UUID primary keys, UTC timestamps, and strict multi-tenant organization scoping.
"""
import uuid
from django.core.exceptions import ValidationError
from django.db import models


class UUIDModel(models.Model):
    """Abstract model providing a UUIDv4 primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(UUIDModel):
    """Abstract model providing created_at and updated_at timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantScopedQuerySet(models.QuerySet):
    """QuerySet helper that forces explicit organization and project filtering."""

    def for_organization(self, organization_id):
        if not organization_id:
            return self.none()
        return self.filter(organization_id=organization_id)

    def for_project(self, organization_id, project_id):
        if not organization_id or not project_id:
            return self.none()
        return self.filter(organization_id=organization_id, project_id=project_id)


class TenantScopedModel(TimestampedModel):
    """
    Abstract base model for all tenant-owned records.
    Every tenant-owned record has an explicit organization boundary.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_records",
        db_index=True,
    )

    objects = TenantScopedQuerySet.as_manager()

    class Meta:
        abstract = True


class ProjectScopedModel(TenantScopedModel):
    """
    Abstract base model for project-owned records.
    Validates that project.organization_id matches self.organization_id to prevent cross-tenant leakage.
    """

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_records",
        db_index=True,
    )

    class Meta:
        abstract = True

    def clean(self) -> None:
        super().clean()
        if self.project_id and self.organization_id:
            if self.project.organization_id != self.organization_id:
                raise ValidationError(
                    "Cross-tenant integrity violation: Project does not belong to the specified Organization."
                )

    def save(self, *args, **kwargs):
        if self.project_id and not self.organization_id:
            self.organization_id = self.project.organization_id
        self.full_clean()
        return super().save(*args, **kwargs)

