from django.urls import path
from .views import (
    AdminLoginView,
    LoginView,
    LogoutView,
    PasswordResetRequestView,
    RegisterView,
    TokenRefreshView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("admin/login/", AdminLoginView.as_view(), name="auth-admin-login"),
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
]
