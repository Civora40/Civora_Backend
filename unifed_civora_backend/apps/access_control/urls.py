from django.urls import path
from .views import PermissionListView, RoleListView

urlpatterns = [
    path("roles/", RoleListView.as_view(), name="access-control-roles"),
    path("permissions/", PermissionListView.as_view(), name="access-control-permissions"),
]

