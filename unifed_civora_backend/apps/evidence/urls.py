"""URL patterns for Evidence media and attachments (Phase 5)."""
from django.urls import path
from .views import (
    EvidenceDetailView,
    EvidenceDownloadView,
    EvidenceListView,
    EvidenceUploadView,
    EvidenceVerifyView,
)

urlpatterns = [
    path("", EvidenceListView.as_view(), name="evidence-list"),
    path("upload/", EvidenceUploadView.as_view(), name="evidence-upload"),
    path("<uuid:attachment_id>/", EvidenceDetailView.as_view(), name="evidence-detail"),
    path("<uuid:attachment_id>/download/", EvidenceDownloadView.as_view(), name="evidence-download"),
    path("<uuid:attachment_id>/verify/", EvidenceVerifyView.as_view(), name="evidence-verify"),
]
