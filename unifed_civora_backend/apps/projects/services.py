"""Write services for Projects domain."""
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.organizations.models import OrganizationMembership
from .models import Project, ProjectDepartment, ProjectMembership, ProjectStatus


@transaction.atomic
def create_project(
    *,
    organization,
    name: str,
    code: str,
    location: str = "",
    description: str = "",
    status: str = ProjectStatus.DRAFT,
    created_by=None,
) -> Project:
    return Project.objects.create(
        organization=organization,
        name=name,
        code=code.upper(),
        location=location,
        description=description,
        status=status,
        created_by=created_by,
    )


@transaction.atomic
def transition_project_status(*, project: Project, new_status: str) -> Project:
    if not project.can_transition_to(new_status):
        raise ValidationError(
            f"Invalid project lifecycle transition from '{project.status}' to '{new_status}'."
        )
    project.status = new_status
    project.save(update_fields=["status", "updated_at"])
    return project


@transaction.atomic
def create_project_department(
    *,
    project: Project,
    name: str,
    code: str,
    description: str = "",
) -> ProjectDepartment:
    return ProjectDepartment.objects.create(
        organization=project.organization,
        project=project,
        name=name,
        code=code.upper(),
        description=description,
    )


@transaction.atomic
def assign_user_to_project(
    *,
    project: Project,
    user,
    role,
    department: ProjectDepartment = None,
) -> ProjectMembership:
    # Enforce that user belongs to the project's owning organization
    is_org_member = OrganizationMembership.objects.filter(
        organization_id=project.organization_id,
        user=user,
        is_active=True,
    ).exists()
    if not is_org_member:
        raise ValidationError(
            "Cannot assign user to project: user is not an active member of the owning organization."
        )

    membership, _ = ProjectMembership.objects.update_or_create(
        organization=project.organization,
        project=project,
        user=user,
        defaults={
            "role": role,
            "department": department,
            "is_active": True,
        },
    )
    return membership

