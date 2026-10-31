"""DRF Permission classes enforcing tenant and project RBAC boundaries."""
from rest_framework.permissions import BasePermission
from common.middleware.tenant import resolve_request_organization
from .selectors import user_has_organization_permission, user_has_project_permission


class IsTenantMember(BasePermission):
    """Requires the user to have an active membership in the request's organization."""

    message = "You do not have access to this organization."

    def has_permission(self, request, view) -> bool:
        org = resolve_request_organization(request)
        return bool(
            request.user
            and request.user.is_authenticated
            and org is not None
        )


class HasOrganizationCapability(BasePermission):
    """Checks `required_permission` attribute on the view against the user's organization role."""

    def has_permission(self, request, view) -> bool:
        required_perm = getattr(view, "required_permission", None)
        org = resolve_request_organization(request)
        if not org:
            return False
        if not required_perm:
            return True
        return user_has_organization_permission(request.user, org.id, required_perm)


class HasProjectCapability(BasePermission):
    """Checks `required_project_permission` on the view against the user's project membership."""

    def has_permission(self, request, view) -> bool:
        required_perm = getattr(view, "required_project_permission", None)
        org = resolve_request_organization(request)
        project_id = view.kwargs.get("project_id") or request.query_params.get("project_id")
        if not org or not project_id or not required_perm:
            return False
        return user_has_project_permission(request.user, org.id, project_id, required_perm)
