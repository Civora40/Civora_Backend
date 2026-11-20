from django.urls import path
from .views import (
    SubmissionAttachmentsView,
    SubmissionDetailView,
    SubmissionLinkTaskView,
    SubmissionListCreateView,
    SubmissionRequestInfoView,
    SubmissionTransitionStatusView,
)

urlpatterns = [
    path("", SubmissionListCreateView.as_view(), name="submission-list"),
    path("<uuid:submission_id>/", SubmissionDetailView.as_view(), name="submission-detail"),
    path(
        "<uuid:submission_id>/transition-status/",
        SubmissionTransitionStatusView.as_view(),
        name="submission-transition-status",
    ),
    path(
        "<uuid:submission_id>/link-task/",
        SubmissionLinkTaskView.as_view(),
        name="submission-link-task",
    ),
    path(
        "<uuid:submission_id>/request-info/",
        SubmissionRequestInfoView.as_view(),
        name="submission-request-info",
    ),
    path(
        "<uuid:submission_id>/attachments/",
        SubmissionAttachmentsView.as_view(),
        name="submission-attachments",
    ),
]
