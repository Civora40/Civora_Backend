"""
Root URL configuration for Civora Backend.
Exposes versioned `/api/v1/` endpoints across Mobile, PE/Stakeholder, and Admin frontends,
plus OpenAPI 3.1 schema and Swagger UI documentation.
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from common.views import ApiRootIndexView, HealthCheckView

api_v1_patterns = [
    path("", ApiRootIndexView.as_view(), name="api-v1-root"),
    path("health/", HealthCheckView.as_view(), name="api-health"),
    path("auth/", include("apps.authentication.urls")),
    path("users/", include("apps.users.urls")),
    path("access-control/", include("apps.access_control.urls")),
    path("organizations/", include("apps.organizations.urls")),
    path("projects/", include("apps.projects.urls")),
    path("submissions/", include("apps.submissions.urls")),
    path("evidence/", include("apps.evidence.urls")),
    path("integrations/", include("apps.integrations.urls")),
    path("tasks/", include("apps.tasks.urls")),
    path("performance/", include("apps.performance.urls")),
    path("reports/", include("apps.reports.urls")),
    path("documents/", include("apps.document_management.urls")),
    path("ai/", include("apps.ai_assistance.urls")),
    path("approvals/", include("apps.approvals.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("audit/", include("apps.audit.urls")),
]

urlpatterns = [
    path("", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-root"),
    path("swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-short"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/", include(api_v1_patterns)),
    path("admin/", admin.site.urls),
]

