from django.urls import path
from .views import (
    ProjectDetailView,
    ProjectListView,
    ProjectMemberDetailView,
    ProjectMemberListView,
)

urlpatterns = [
    path("", ProjectListView.as_view(), name="project-list"),
    path("<uuid:project_id>/", ProjectDetailView.as_view(), name="project-detail"),
    path("<uuid:project_id>/members/", ProjectMemberListView.as_view(), name="project-member-list"),
    path(
        "<uuid:project_id>/members/<uuid:member_id>/",
        ProjectMemberDetailView.as_view(),
        name="project-member-detail",
    ),
]
