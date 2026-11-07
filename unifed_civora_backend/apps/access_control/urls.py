from django.urls import path
from .views import (
    PermissionCheckView,
    PermissionListView,
    RoleListView,
    UserPermissionsEvaluationView,
)

urlpatterns = [
    path("roles/", RoleListView.as_view(), name="access-control-roles"),
    path("permissions/", PermissionListView.as_view(), name="access-control-permissions"),
    path("my-permissions/", UserPermissionsEvaluationView.as_view(), name="access-control-my-permissions"),
    path("evaluate/", PermissionCheckView.as_view(), name="access-control-evaluate"),
]
