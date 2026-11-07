from django.urls import path
from .views import (
    AdminLoginView,
    EmailVerificationConfirmView,
    EmailVerificationRequestView,
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    SessionListView,
    SessionRevokeView,
    TokenRefreshView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("admin/login/", AdminLoginView.as_view(), name="auth-admin-login"),
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("sessions/", SessionListView.as_view(), name="auth-sessions"),
    path("sessions/<uuid:session_id>/revoke/", SessionRevokeView.as_view(), name="auth-session-revoke"),
    path("verify-email/request/", EmailVerificationRequestView.as_view(), name="auth-verify-email-request"),
    path("verify-email/confirm/", EmailVerificationConfirmView.as_view(), name="auth-verify-email-confirm"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="auth-password-reset-confirm"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
]
