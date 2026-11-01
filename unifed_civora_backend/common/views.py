"""
System health and readiness check views for Civora Backend (Phase 1 Deliverable).
"""
from django.conf import settings
from django.db import connections
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from common.responses import api_error, api_success


class HealthCheckView(APIView):
    """
    GET /api/v1/health/
    Verifies API service status and database connectivity.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["System & Health"],
        summary="Health Check & Database Connectivity",
        description="Verifies that the Civora API server is running and PostgreSQL is reachable.",
        responses={
            200: inline_serializer(
                name="HealthCheckResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "message": serializers.CharField(default="Civora Backend is healthy."),
                    "data": serializers.DictField(),
                },
            )
        },
    )
    def get(self, request):
        db_ok = False
        db_engine = settings.DATABASES["default"]["ENGINE"].split(".")[-1]
        try:
            conn = connections["default"]
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1;")
                row = cursor.fetchone()
                db_ok = bool(row and row[0] == 1)
        except Exception as exc:
            return api_error(
                message="Database connectivity check failed.",
                code="database_unavailable",
                details={"database": str(exc)},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return api_success(
            data={
                "service": "civora-backend",
                "environment": getattr(settings, "CIVORA_ENV", "development"),
                "version": "1.0.0",
                "database": {
                    "connected": db_ok,
                    "engine": db_engine,
                },
            },
            message="Civora Backend is healthy.",
        )


class ApiRootIndexView(APIView):
    """
    GET /api/v1/
    Returns service metadata and endpoint discovery links.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["System & Health"],
        summary="API v1 Discovery Index",
        description="Lists core API v1 endpoints and documentation links.",
        responses={
            200: inline_serializer(
                name="ApiRootIndexResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            )
        },
    )
    def get(self, request):
        base_url = request.build_absolute_uri("/").rstrip("/")
        return api_success(
            data={
                "service": "Civora Centralized Reporting Hub API",
                "status": "online",
                "version": "1.0.0",
                "environment": getattr(settings, "CIVORA_ENV", "development"),
                "endpoints": {
                    "swagger_ui": f"{base_url}/",
                    "swagger_docs": f"{base_url}/api/docs/",
                    "redoc": f"{base_url}/api/redoc/",
                    "openapi_schema": f"{base_url}/api/schema/",
                    "health": f"{base_url}/api/v1/health/",
                    "auth_login": f"{base_url}/api/v1/auth/login/",
                    "admin_login": f"{base_url}/api/v1/auth/admin/login/",
                    "auth_register": f"{base_url}/api/v1/auth/register/",
                    "projects": f"{base_url}/api/v1/projects/",
                    "organizations": f"{base_url}/api/v1/organizations/",
                    "users_me": f"{base_url}/api/v1/users/me/",
                },
            },
            message="Welcome to the Civora Unified Backend API.",
        )
