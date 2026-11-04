from django.urls import path
from .views import ReportDetailView, ReportListCreateView, ReportTemplateListCreateView

urlpatterns = [
    path("", ReportListCreateView.as_view(), name="report-list"),
    path("templates/", ReportTemplateListCreateView.as_view(), name="report-template-list"),
    path("<uuid:report_id>/", ReportDetailView.as_view(), name="report-detail"),
]
