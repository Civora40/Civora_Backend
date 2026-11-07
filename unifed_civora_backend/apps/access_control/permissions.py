"""DRF Permission classes enforcing tenant and project RBAC boundaries (Phase 3)."""
from rest_framework.permissions import BasePermission
from apps.audit.services import record_audit_event
from common.middleware.tenant import resolve_request_organization
from .selectors import user_has_organization_permission, user_has_project_permission


class IsTenantMember(BasePermission):
    """Requires the user to have an active membership in the request's organization."""

    message = "You do not have access to this organization."

    def has_permission(self, request, view) -> bool:
        org = resolve_request_organization(request)
        if (
            request.user
            and request.user.is_authenticated
            and org is not None
        ):
            return True
        if request.user and request.user.is_authenticated and request.headers.get("X-Organization-ID"):
            record_audit_event(
                action="rbac.tenant_access_denied",
                target_type="Organization",
                target_id=str(request.headers.get("X-Organization-ID")),
                actor=request.user,
                metadata={"requested_organization_id": request.headers.get("X-Organization-ID")},
            )
        return False


class HasOrganizationCapability(BasePermission):
    """Checks `required_permission` attribute on the view against the user's organization role."""

    message = "You do not hold the required organization capability for this action."

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        required_perm = getattr(view, "required_permission", None)
        org = resolve_request_organization(request)
        if getattr(request.user, "is_platform_admin", False) and not request.headers.get("X-Organization-ID"):
            return True
        if not org:
            return False
        if not required_perm:
            return True
        allowed = user_has_organization_permission(request.user, org.id, required_perm)
        if not allowed:
            record_audit_event(
                action="rbac.permission_denied",
                target_type="Organization",
                target_id=str(org.id),
                actor=request.user,
                organization=org,
                metadata={"required_permission": required_perm, "scope": "organization"},
            )
        return allowed


class HasProjectCapability(BasePermission):
    """Checks `required_project_permission` on the view against the user's project membership."""

    message = "You do not hold the required project capability for this action."

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        required_perm = getattr(view, "required_project_permission", None)
        org = resolve_request_organization(request)
        project_id = (
            view.kwargs.get("project_id")
            or request.query_params.get("project_id")
            or request.query_params.get("projectId")
        )
        if not org or not project_id or not required_perm:
            return False
        allowed = user_has_project_permission(request.user, org.id, project_id, required_perm)
        if not allowed:
            record_audit_event(
                action="rbac.permission_denied",
                target_type="Project",
                target_id=str(project_id),
                actor=request.user,
                organization=org,
                metadata={"required_permission": required_perm, "scope": "project", "project_id": str(project_id)},
            )
        return allowed
