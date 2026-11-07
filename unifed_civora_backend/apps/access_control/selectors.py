"""Permission evaluation selectors for Civora RBAC (Phase 3)."""
from apps.organizations.models import OrganizationMembership
from apps.projects.models import ProjectMembership
from .models import Permission, SystemRoleSlug


def user_has_organization_permission(user, organization_id, permission_codename: str) -> bool:
    """Verify if a user holds an explicit permission within a tenant organization."""
    if not user or not user.is_authenticated or not organization_id:
        return False
    if not user.is_active or getattr(user, "account_status", "active") == "suspended":
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
    and holds the required capability on their project role (or tenant admin role).
    """
    if not user or not user.is_authenticated or not organization_id or not project_id:
        return False
    if not user.is_active or getattr(user, "account_status", "active") == "suspended":
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
    if not org_membership or not org_membership.role:
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
    if proj_membership and proj_membership.role:
        return proj_membership.role.permissions.filter(codename=permission_codename).exists()

    # Organization Admins & Platform Admins who belong to the tenant can manage tenant projects
    if org_membership.role.slug in (SystemRoleSlug.PLATFORM_ADMIN, SystemRoleSlug.ORGANIZATION_ADMIN):
        from apps.projects.models import Project

        if Project.objects.filter(id=project_id, organization_id=organization_id).exists():
            return org_membership.role.permissions.filter(codename=permission_codename).exists()

    return False


def get_user_effective_permissions(user, organization_id, project_id=None) -> dict:
    """
    Returns a complete capability matrix for `user` in `organization_id` and optional `project_id`.
    """
    if not user or not user.is_authenticated or not organization_id:
        return {
            "organizationId": str(organization_id) if organization_id else None,
            "organizationRole": None,
            "organizationPermissions": [],
            "projectRole": None,
            "projectPermissions": [],
            "projectMemberships": [],
        }

    org_membership = (
        OrganizationMembership.objects.select_related("role")
        .prefetch_related("role__permissions")
        .filter(
            user=user,
            organization_id=organization_id,
            is_active=True,
            organization__is_active=True,
        )
        .first()
    )

    org_role = org_membership.role.slug if (org_membership and org_membership.role) else (
        SystemRoleSlug.PLATFORM_ADMIN if user.is_platform_admin else None
    )
    if org_membership and org_membership.role:
        org_perms = sorted(p.codename for p in org_membership.role.permissions.all())
    elif user.is_platform_admin:
        org_perms = sorted(Permission.objects.values_list("codename", flat=True))
    else:
        org_perms = []

    proj_memberships = list(
        ProjectMembership.objects.select_related("role", "project")
        .prefetch_related("role__permissions")
        .filter(user=user, organization_id=organization_id, is_active=True)
    )
    project_summary = [
        {
            "projectId": str(pm.project_id),
            "projectCode": pm.project.code if pm.project else "",
            "role": pm.role.slug if pm.role else "",
            "permissions": sorted(p.codename for p in pm.role.permissions.all()) if pm.role else [],
        }
        for pm in proj_memberships
    ]

    proj_role = None
    proj_perms = []
    if project_id:
        matching_pm = next((pm for pm in proj_memberships if str(pm.project_id) == str(project_id)), None)
        if matching_pm and matching_pm.role:
            proj_role = matching_pm.role.slug
            proj_perms = sorted(p.codename for p in matching_pm.role.permissions.all())
        elif org_role in (SystemRoleSlug.PLATFORM_ADMIN, SystemRoleSlug.ORGANIZATION_ADMIN):
            proj_role = org_role
            proj_perms = org_perms

    return {
        "organizationId": str(organization_id),
        "organizationRole": org_role,
        "organizationPermissions": org_perms,
        "projectId": str(project_id) if project_id else None,
        "projectRole": proj_role,
        "projectPermissions": proj_perms,
        "projectMemberships": project_summary,
    }
