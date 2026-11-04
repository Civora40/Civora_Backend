from django.urls import path
from .views import (
    ChangePasswordView,
    CurrentUserProfileView,
    UserListView,
    UserOnboardingView,
    UserToggleStatusView,
)

urlpatterns = [
    path("", UserListView.as_view(), name="users-list"),
    path("me/", CurrentUserProfileView.as_view(), name="users-me"),
    path("me/change-password/", ChangePasswordView.as_view(), name="users-change-password"),
    path("onboarding/", UserOnboardingView.as_view(), name="users-onboarding"),
    path("<uuid:user_id>/toggle-status/", UserToggleStatusView.as_view(), name="users-toggle-status"),
]
