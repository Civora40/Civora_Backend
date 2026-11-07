"""Authentication HTTP views (Phase 3 — Authentication and Authorization) with OpenAPI schemas."""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView
from apps.audit.services import record_audit_event
from apps.users.serializers import serialize_civora_user
from common.responses import api_success
from .models import Session
from .serializers import (
    EmailVerificationConfirmSerializer,
    EmailVerificationRequestSerializer,
    LoginRequestSerializer,
    LogoutRequestSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterRequestSerializer,
)
from .services import (
    authenticate_and_issue_tokens,
    confirm_email_verification,
    confirm_password_reset,
    extract_client_ip,
    register_user_account,
    request_email_verification,
    request_password_reset,
    revoke_user_sessions,
)

AuthTokenEnvelopeSerializer = inline_serializer(
    name="AuthTokenEnvelope",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": inline_serializer(
            name="AuthTokenData",
            fields={
                "access": serializers.CharField(help_text="JWT Bearer access token"),
                "refresh": serializers.CharField(help_text="JWT refresh token"),
                "sessionId": serializers.CharField(required=False),
                "user": serializers.DictField(help_text="CivoraUser profile object"),
                "redirectUrl": serializers.CharField(required=False),
            },
        ),
    },
)


def serialize_session(sess: Session) -> dict:
    return {
        "id": str(sess.id),
        "userId": str(sess.user_id),
        "userEmail": sess.user.email if sess.user else "",
        "organizationId": str(sess.organization_id) if sess.organization_id else None,
        "jti": sess.jti,
        "clientSurface": sess.client_surface,
        "ipAddress": sess.ip_address,
        "userAgent": sess.user_agent,
        "isRevoked": sess.is_revoked,
        "revokedAt": sess.revoked_at.isoformat() if sess.revoked_at else None,
        "expiresAt": sess.expires_at.isoformat() if sess.expires_at else None,
        "lastActiveAt": sess.last_active_at.isoformat() if sess.last_active_at else None,
        "createdAt": sess.created_at.isoformat() if sess.created_at else None,
    }


class LoginView(APIView):
    """POST /api/v1/auth/login/"""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = LoginRequestSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="User Sign-In (PE, Stakeholder & Admin)",
        description=(
            "Authenticates a user with email and password and issues a JWT `access` and `refresh` token pair "
            "along with the hydrated `CivoraUser` profile."
        ),
        request=LoginRequestSerializer,
        responses={200: AuthTokenEnvelopeSerializer},
        examples=[
            OpenApiExample(
                "Project Engineer Login",
                value={"email": "engineer@civora.internal", "password": "Civora2026!"},
                request_only=True,
            ),
            OpenApiExample(
                "Stakeholder Login",
                value={"email": "stakeholder@civora.internal", "password": "Civora2026!"},
                request_only=True,
            ),
        ],
    )
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
    serializer_class = LoginRequestSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Dedicated Platform Admin Sign-In",
        description=(
            "Authenticates a Platform Administrator for the Civora Admin Console. "
            "Rejects non-admin accounts with `403 Forbidden` and logs an immutable security audit event."
        ),
        request=LoginRequestSerializer,
        responses={200: AuthTokenEnvelopeSerializer},
        examples=[
            OpenApiExample(
                "Dedicated Platform Admin",
                value={"email": "Civora40@gmail.com", "password": "v;H#bRZby'Vc#0mT"},
                request_only=True,
            ),
        ],
    )
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
    serializer_class = RegisterRequestSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Register User Account (Controlled Onboarding)",
        description=(
            "Registers a new Project Engineer or Read-Only Stakeholder account, validates optional "
            "organization invitation codes (`CIV-INV-...`), prevents self-assigned `platform_admin` "
            "escalation, and returns JWT tokens."
        ),
        request=RegisterRequestSerializer,
        responses={201: AuthTokenEnvelopeSerializer},
        examples=[
            OpenApiExample(
                "Project Engineer Registration",
                value={
                    "firstName": "Kemi",
                    "lastName": "Alabi",
                    "email": "k.alabi@juliusberger.com",
                    "password": "StrongPassword2026!",
                    "organization": "Julius Berger Nigeria Plc",
                    "jobTitle": "Senior Structural Engineer",
                    "invitationCode": "CIV-INV-7841",
                    "role": "project_engineer",
                },
                request_only=True,
            ),
        ],
    )
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
            request=request,
        )
        return api_success(
            data=result,
            message="Registration successful.",
            status_code=status.HTTP_201_CREATED,
        )


class LogoutView(APIView):
    """POST /api/v1/auth/logout/"""

    permission_classes = [AllowAny]
    serializer_class = LogoutRequestSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Sign Out, Revoke Session & Audit Termination",
        request=LogoutRequestSerializer,
        responses={
            200: inline_serializer(
                name="LogoutResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request):
        user = getattr(request, "user", None)
        body = request.data if isinstance(request.data, dict) else {}
        auth_token = getattr(request, "auth", None)
        token_jti = None
        if auth_token is not None and hasattr(auth_token, "get"):
            token_jti = str(auth_token.get("jti", "")) or None

        jti = body.get("jti") or token_jti
        session_id = body.get("sessionId") or body.get("session_id")
        revoke_all = bool(body.get("revokeAll") or body.get("revoke_all"))

        revoked_count = 0
        if (user and user.is_authenticated) or jti or session_id:
            revoked_count = revoke_user_sessions(
                user=user if (user and user.is_authenticated) else None,
                jti=jti,
                session_id=session_id,
                revoke_all=revoke_all,
                actor=user if (user and user.is_authenticated) else None,
                request=request,
            )

        if user and user.is_authenticated:
            record_audit_event(
                action="auth.logout",
                target_type="User",
                target_id=str(user.id),
                actor=user,
                ip_address=extract_client_ip(request),
                metadata={"revoked_sessions": revoked_count, "jti": jti},
            )
        return api_success(
            data={"logged_out": True, "revokedSessions": revoked_count},
            message="Signed out successfully.",
        )


class SessionListView(APIView):
    """GET /api/v1/auth/sessions/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Authentication"],
        summary="List Tracked Authentication Sessions",
        description="Lists active and historical sessions for the current user (or across users for Platform Admins).",
    )
    def get(self, request):
        if request.user.is_platform_admin and request.query_params.get("all") == "true":
            qs = Session.objects.select_related("user", "organization").all()[:100]
        else:
            qs = Session.objects.select_related("user", "organization").filter(user=request.user)[:50]
        return api_success(data=[serialize_session(s) for s in qs])


class SessionRevokeView(APIView):
    """POST, DELETE /api/v1/auth/sessions/<uuid:session_id>/revoke/"""

    permission_classes = [IsAuthenticated]

    def _revoke(self, request, session_id):
        sess = get_object_or_404(Session.objects.select_related("user", "organization"), id=session_id)
        if not request.user.is_platform_admin and sess.user_id != request.user.id:
            raise PermissionDenied("You can only revoke your own authentication sessions.")

        sess.revoke()
        record_audit_event(
            action="auth.session_revoked",
            target_type="Session",
            target_id=str(sess.id),
            actor=request.user,
            organization=sess.organization,
            ip_address=extract_client_ip(request),
            metadata={"jti": sess.jti, "user_email": sess.user.email},
        )
        return api_success(data=serialize_session(sess), message="Session revoked.")

    @extend_schema(tags=["Authentication"], summary="Revoke a Tracked Session by ID")
    def post(self, request, session_id):
        return self._revoke(request, session_id)

    @extend_schema(tags=["Authentication"], summary="Revoke a Tracked Session by ID (DELETE)")
    def delete(self, request, session_id):
        return self._revoke(request, session_id)


class EmailVerificationRequestView(APIView):
    """POST /api/v1/auth/verify-email/request/"""

    permission_classes = [AllowAny]
    serializer_class = EmailVerificationRequestSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Request Email Verification Token",
        request=EmailVerificationRequestSerializer,
    )
    def post(self, request):
        serializer = EmailVerificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = getattr(request, "user", None)
        email = serializer.validated_data.get("email") or (user.email if user and user.is_authenticated else "")
        if not email:
            raise ValidationError({"email": ["Email address is required."]})
        result = request_email_verification(
            email=email,
            actor=user if (user and user.is_authenticated) else None,
            request=request,
        )
        return api_success(
            data=result,
            message="Email verification instructions dispatched.",
        )


class EmailVerificationConfirmView(APIView):
    """POST /api/v1/auth/verify-email/confirm/"""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = EmailVerificationConfirmSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Confirm Email Verification Token",
        request=EmailVerificationConfirmSerializer,
    )
    def post(self, request):
        serializer = EmailVerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = confirm_email_verification(
            token=serializer.validated_data["token"],
            email=serializer.validated_data.get("email", ""),
            request=request,
        )
        return api_success(
            data={"verified": True, "user": serialize_civora_user(user)},
            message="Email address verified successfully.",
        )


class PasswordResetRequestView(APIView):
    """POST /api/v1/auth/password-reset/"""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PasswordResetRequestSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Request Password Recovery Link",
        request=PasswordResetRequestSerializer,
        responses={
            200: inline_serializer(
                name="PasswordResetResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = request_password_reset(
            email=serializer.validated_data["email"],
            request=request,
        )
        return api_success(
            data=result,
            message="If an account matches that email, a password recovery link has been dispatched.",
        )


class PasswordResetConfirmView(APIView):
    """POST /api/v1/auth/password-reset/confirm/"""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PasswordResetConfirmSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Confirm Password Reset with Token",
        request=PasswordResetConfirmSerializer,
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        new_pw = d.get("newPassword") or d.get("new_password") or ""
        user = confirm_password_reset(
            token=d["token"],
            new_password=new_pw,
            email=d.get("email", ""),
            request=request,
        )
        return api_success(
            data={"reset": True, "email": user.email},
            message="Password has been reset successfully. All active sessions have been revoked.",
        )


__all__ = [
    "LoginView",
    "AdminLoginView",
    "RegisterView",
    "LogoutView",
    "SessionListView",
    "SessionRevokeView",
    "EmailVerificationRequestView",
    "EmailVerificationConfirmView",
    "PasswordResetRequestView",
    "PasswordResetConfirmView",
    "TokenRefreshView",
]
