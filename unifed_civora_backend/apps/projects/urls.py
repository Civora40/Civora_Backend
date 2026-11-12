from django.urls import path
from .views import (
    ProjectActivityDetailView,
    ProjectActivityHistoryView,
    ProjectActivityListView,
    ProjectDepartmentDetailView,
    ProjectDepartmentListView,
    ProjectDetailView,
    ProjectListView,
    ProjectMemberDetailView,
    ProjectMemberListView,
    ProjectReviewView,
    ProjectStatusTransitionView,
    ProjectSubmitForApprovalView,
)

urlpatterns = [
    path("", ProjectListView.as_view(), name="project-list"),
    path("<uuid:project_id>/", ProjectDetailView.as_view(), name="project-detail"),
    path("<uuid:project_id>/submit-for-approval/", ProjectSubmitForApprovalView.as_view(), name="project-submit-for-approval"),
    path("<uuid:project_id>/review/", ProjectReviewView.as_view(), name="project-review"),
    path("<uuid:project_id>/transition-status/", ProjectStatusTransitionView.as_view(), name="project-transition-status"),
    path("<uuid:project_id>/members/", ProjectMemberListView.as_view(), name="project-member-list"),
    path(
        "<uuid:project_id>/members/<uuid:member_id>/",
        ProjectMemberDetailView.as_view(),
        name="project-member-detail",
    ),
    path("<uuid:project_id>/departments/", ProjectDepartmentListView.as_view(), name="project-department-list"),
    path(
        "<uuid:project_id>/departments/<uuid:department_id>/",
        ProjectDepartmentDetailView.as_view(),
        name="project-department-detail",
    ),
    path("<uuid:project_id>/activities/", ProjectActivityListView.as_view(), name="project-activity-list"),
    path(
        "<uuid:project_id>/activities/<uuid:activity_id>/",
        ProjectActivityDetailView.as_view(),
        name="project-activity-detail",
    ),
    path(
        "<uuid:project_id>/activity-history/",
        ProjectActivityHistoryView.as_view(),
        name="project-activity-history",
    ),
]
