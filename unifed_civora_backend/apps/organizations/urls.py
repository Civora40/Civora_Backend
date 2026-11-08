from django.urls import path
from .views import (
    InvitationAcceptView,
    InvitationListCreateView,
    InvitationRevokeView,
    MembershipListView,
    MembershipRevokeView,
    OrganizationDetailView,
    OrganizationListView,
)

urlpatterns = [
    path("", OrganizationListView.as_view(), name="organization-list"),
    path("memberships/", MembershipListView.as_view(), name="organization-membership-list"),
    path("memberships/<uuid:membership_id>/", MembershipRevokeView.as_view(), name="organization-membership-revoke"),
    path("invitations/", InvitationListCreateView.as_view(), name="organization-invitation-list"),
    path("invitations/accept/", InvitationAcceptView.as_view(), name="organization-invitation-accept"),
    path("invitations/<uuid:invitation_id>/revoke/", InvitationRevokeView.as_view(), name="organization-invitation-revoke"),
    path("<uuid:org_id>/", OrganizationDetailView.as_view(), name="organization-detail"),
]
