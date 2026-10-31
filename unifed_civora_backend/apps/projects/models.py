"""
Project, ProjectDepartment, and ProjectMembership models (Phase 2 & 4).
Enforces tenant and project boundaries and controlled lifecycle transitions:
Draft -> Setup -> Active -> On Hold -> Completed -> Archived.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from common.models import ProjectScopedModel, TenantScopedModel


class ProjectStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SETUP = "setup", "Setup"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On Hold"
    COMPLETED = "completed", "Completed"
    ARCHIVED = "archived", "Archived"


ALLOWED_PROJECT_TRANSITIONS = {
    ProjectStatus.DRAFT: {ProjectStatus.SETUP, ProjectStatus.ARCHIVED},
    ProjectStatus.SETUP: {ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD, ProjectStatus.ARCHIVED},
    ProjectStatus.ACTIVE: {ProjectStatus.ON_HOLD, ProjectStatus.COMPLETED},
    ProjectStatus.ON_HOLD: {ProjectStatus.ACTIVE, ProjectStatus.ARCHIVED},
    ProjectStatus.COMPLETED: {ProjectStatus.ARCHIVED},
    ProjectStatus.ARCHIVED: set(),
}


class Project(TenantScopedModel):
    """
    Core construction or engineering project owned by an Organization.
    """

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=60, db_index=True)
    client_name = models.CharField(max_length=255, blank=True, default="")
    location = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=30,
        choices=ProjectStatus.choices,
        default=ProjectStatus.DRAFT,
        db_index=True,
    )
    lead_engineer_name = models.CharField(max_length=255, blank=True, default="")
    lasbca_permit_number = models.CharField(max_length=120, blank=True, default="")
    budget_formatted = models.CharField(max_length=80, blank=True, default="")
    specs_reference = models.CharField(max_length=255, blank=True, default="")
    progress_percent = models.PositiveSmallIntegerField(default=0)
    project_metadata = models.JSONField(default=dict, blank=True)
    start_date = models.DateField(null=True, blank=True)
    target_completion_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_projects",
    )

    class Meta:
        db_table = "civora_projects"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="uq_organization_project_code",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="idx_project_org_status"),
        ]

    def can_transition_to(self, new_status: str) -> bool:
        if new_status == self.status:
            return True
        return new_status in ALLOWED_PROJECT_TRANSITIONS.get(self.status, set())

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"


class ProjectDepartment(ProjectScopedModel):
    """
    Department or professional engineering discipline within a Project
    (e.g., Structural, Geotechnical, MEP, HSE, QA/QC).
    """

    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40)
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "civora_project_departments"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "code"],
                name="uq_project_department_code",
            )
        ]

    def __str__(self) -> str:
        return f"{self.project.code} / {self.name} ({self.code})"


class ProjectMembership(ProjectScopedModel):
    """
    Explicit project-level access assignment linking a User, Role, and optional Department to a Project.
    Ensures the user is also a member of the owning Organization.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
        db_index=True,
    )
    role = models.ForeignKey(
        "access_control.Role",
        on_delete=models.PROTECT,
        related_name="project_memberships",
    )
    department = models.ForeignKey(
        ProjectDepartment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memberships",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "civora_project_memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"],
                name="uq_project_user_membership",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "project", "is_active"],
                name="idx_proj_member_active",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.department_id and self.project_id:
            if self.department.project_id != self.project_id:
                raise ValidationError(
                    "Department does not belong to the specified Project."
                )

    def __str__(self) -> str:
        return f"{self.user.email} -> {self.project.code} ({self.role.slug})"

