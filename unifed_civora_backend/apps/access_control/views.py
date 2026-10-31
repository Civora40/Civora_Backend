"""HTTP views for Access Control (RBAC Roles & Permissions) domain."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from common.responses import api_success
from .models import Permission, Role


class RoleListView(APIView):
    """GET /api/v1/access-control/roles/"""

    permission_classes = [IsAuthenticated]

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

