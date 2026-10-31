"""Read selectors for Projects domain enforcing tenant and membership isolation."""
from django.db.models import QuerySet
from .models import Project, ProjectMembership


def get_authorized_projects_for_user(*, user, organization_id) -> QuerySet[Project]:
    """
    Returns projects scoped strictly to `organization_id` that `user` is an active member of.
    Prevents cross-tenant and unauthorized cross-project access.
    """
    if not user or not user.is_authenticated or not organization_id:
        return Project.objects.none()

    return Project.objects.for_organization(organization_id).filter(
        projects_projectmembership_records__user=user,
        projects_projectmembership_records__is_active=True,
    ).distinct()


def get_project_members(*, organization_id, project_id) -> QuerySet[ProjectMembership]:
    if not organization_id or not project_id:
        return ProjectMembership.objects.none()
    return ProjectMembership.objects.for_project(organization_id, project_id).select_related(
        "user", "role", "department"
    )

