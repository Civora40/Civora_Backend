"""HTTP views for Access Control (RBAC Roles & Permissions) domain with Swagger schemas."""
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from common.responses import api_success
from .models import Permission, Role


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
