from django.urls import path
from .views import ProjectDetailView, ProjectListView, ProjectMemberListView

urlpatterns = [
    path("", ProjectListView.as_view(), name="project-list"),
    path("<uuid:project_id>/", ProjectDetailView.as_view(), name="project-detail"),
    path("<uuid:project_id>/members/", ProjectMemberListView.as_view(), name="project-member-list"),
]
