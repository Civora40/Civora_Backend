from django.urls import path
from .views import SubmissionDetailView, SubmissionListCreateView

urlpatterns = [
    path("", SubmissionListCreateView.as_view(), name="submission-list"),
    path("<uuid:submission_id>/", SubmissionDetailView.as_view(), name="submission-detail"),
]
