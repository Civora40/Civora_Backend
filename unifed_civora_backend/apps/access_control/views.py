"""HTTP views for Access Control (RBAC Roles, Permissions & Capability Evaluation) domain."""
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from common.middleware.tenant import resolve_request_organization
from common.responses import api_success
from .models import Permission, Role
from .selectors import (
    get_user_effective_permissions,
    user_has_organization_permission,
    user_has_project_permission,
)


class RoleListView(APIView):
    """GET /api/v1/access-control/roles/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Access Control (RBAC)"],
        summary="List Canonical System Roles & Capabilities",
        responses={
            200: inline_serializer(
                name="RoleListResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "data": serializers.ListField(child=serializers.DictField()),
                },
            )
        },
    )
    def get(self, request):
        roles = Role.objects.prefetch_related("permissions").all()
        data = [
            {
                "id": str(r.id),
                "slug": r.slug,
                "name": r.name,
                "description": r.description,
                "is_system_role": r.is_system_role,
                "permissions": [p.codename for p in r.permissions.all()],
            }
            for r in roles
        ]
        return api_success(data=data)


class PermissionListView(APIView):
    """GET /api/v1/access-control/permissions/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Access Control (RBAC)"],
        summary="List Explicit Permission Capabilities",
        responses={
            200: inline_serializer(
                name="PermissionListResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "data": serializers.ListField(child=serializers.DictField()),
                },
            )
        },
    )
    def get(self, request):
        perms = Permission.objects.all()
        data = [
            {
                "id": str(p.id),
                "codename": p.codename,
                "name": p.name,
                "module": p.module,
                "description": p.description,
            }
            for p in perms
        ]
        return api_success(data=data)


class UserPermissionsEvaluationView(APIView):
    """GET /api/v1/access-control/my-permissions/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Access Control (RBAC)"],
        summary="Get Current User's Effective Organization & Project Capabilities",
    )
    def get(self, request):
        org = resolve_request_organization(request)
        org_id = org.id if org else None
        project_id = request.query_params.get("projectId") or request.query_params.get("project_id")
        matrix = get_user_effective_permissions(request.user, org_id, project_id=project_id)
        return api_success(data=matrix)


class PermissionCheckView(APIView):
    """POST /api/v1/access-control/evaluate/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Access Control (RBAC)"],
        summary="Evaluate Explicit Organization or Project Capability for Current User",
        request=inline_serializer(
            name="PermissionEvaluateRequest",
            fields={
                "permission": serializers.CharField(),
                "projectId": serializers.UUIDField(required=False),
            },
        ),
    )
    def post(self, request):
        org = resolve_request_organization(request)
        body = request.data or {}
        perm_code = (body.get("permission") or body.get("codename") or "").strip()
        project_id = body.get("projectId") or body.get("project_id")
        if not perm_code:
            raise ValidationError({"permission": ["Permission codename is required."]})

        if not org:
            return api_success(
                data={
                    "allowed": bool(request.user.is_platform_admin),
                    "permission": perm_code,
                    "projectId": str(project_id) if project_id else None,
                    "scope": "platform" if request.user.is_platform_admin else "none",
                }
            )

        if project_id:
            allowed = bool(request.user.is_platform_admin) or user_has_project_permission(
                request.user, org.id, project_id, perm_code
            )
            scope = "project"
        else:
            allowed = bool(request.user.is_platform_admin) or user_has_organization_permission(
                request.user, org.id, perm_code
            )
            scope = "organization"

        return api_success(
            data={
                "allowed": allowed,
                "permission": perm_code,
                "organizationId": str(org.id),
                "projectId": str(project_id) if project_id else None,
                "scope": scope,
            }
        )
