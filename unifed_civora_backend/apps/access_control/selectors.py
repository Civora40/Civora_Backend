"""Permission evaluation selectors for Civora RBAC."""
from apps.organizations.models import OrganizationMembership
from apps.projects.models import ProjectMembership


def user_has_organization_permission(user, organization_id, permission_codename: str) -> bool:
    """Verify if a user holds an explicit permission within a tenant organization."""
    if not user or not user.is_authenticated or not organization_id:
        return False

    membership = (
        OrganizationMembership.objects.select_related("role")
        .filter(
            user=user,
            organization_id=organization_id,
            is_active=True,
            organization__is_active=True,
        )
        .first()
    )
    if not membership or not membership.role:
        return False

    return membership.role.permissions.filter(codename=permission_codename).exists()


def user_has_project_permission(user, organization_id, project_id, permission_codename: str) -> bool:
    """
    Verify if a user is an active member of a project belonging to the tenant organization
    and holds the required capability on their project role (or organization admin role).
    """
    if not user or not user.is_authenticated or not organization_id or not project_id:
        return False

    # Verify tenant membership first
    org_membership = (
        OrganizationMembership.objects.select_related("role")
        .filter(
            user=user,
            organization_id=organization_id,
            is_active=True,
            organization__is_active=True,
        )
        .first()
    )
    if not org_membership:
        return False

    # Check project-specific membership
    proj_membership = (
        ProjectMembership.objects.select_related("role", "project")
        .filter(
            user=user,
            organization_id=organization_id,
            project_id=project_id,
            is_active=True,
        )
        .first()
    )
    if not proj_membership:
        return False

    return proj_membership.role.permissions.filter(codename=permission_codename).exists()

