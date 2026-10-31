"""Authentication HTTP views (Phase 3 / Task 002)."""
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView
from apps.audit.services import record_audit_event
from common.responses import api_success
from .serializers import (
    LoginRequestSerializer,
    PasswordResetRequestSerializer,
    RegisterRequestSerializer,
)
from .services import authenticate_and_issue_tokens, register_user_account


class LoginView(APIView):
    """POST /api/v1/auth/login/"""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = authenticate_and_issue_tokens(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
            require_admin=serializer.validated_data.get("require_admin", False),
            request=request,
        )
        return api_success(data=tokens, message="Authentication successful.")


class AdminLoginView(APIView):
    """
    POST /api/v1/auth/admin/login/
    Dedicated endpoint for Civora_Admin console enforcing `is_platform_admin=True`.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = authenticate_and_issue_tokens(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
            require_admin=True,
            request=request,
        )
        return api_success(data=tokens, message="Admin authentication successful.")


class RegisterView(APIView):
    """POST /api/v1/auth/register/"""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RegisterRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        result = register_user_account(
            email=d["email"],
            password=d["password"],
            first_name=d.get("firstName", ""),
            last_name=d.get("lastName", ""),
            full_name=d.get("full_name", ""),
            organization_name=d.get("organization", ""),
            job_title=d.get("jobTitle", ""),
            invitation_code=d.get("invitationCode", ""),
            requested_role=d.get("role", ""),
            account_type=d.get("accountType", ""),
        )
        return api_success(
            data=result,
            message="Registration successful.",
            status_code=status.HTTP_201_CREATED,
        )


class LogoutView(APIView):
    """POST /api/v1/auth/logout/"""

    permission_classes = [AllowAny]

    def post(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            record_audit_event(
                action="auth.logout",
                target_type="User",
                target_id=str(user.id),
                actor=user,
            )
        return api_success(data={"logged_out": True}, message="Signed out successfully.")


class PasswordResetRequestView(APIView):
    """POST /api/v1/auth/password-reset/"""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        record_audit_event(
            action="auth.password_reset_requested",
            target_type="User",
            target_id=email,
            metadata={"email": email},
        )
        return api_success(
            data={"dispatched": True},
            message="If an account matches that email, a password recovery link has been dispatched.",
        )


__all__ = [
    "LoginView",
    "AdminLoginView",
    "RegisterView",
    "LogoutView",
    "PasswordResetRequestView",
    "TokenRefreshView",
]
