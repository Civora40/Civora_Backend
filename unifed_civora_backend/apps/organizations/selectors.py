"""Read selectors for Organizations domain."""
from django.db.models import QuerySet
from .models import Organization, OrganizationMembership


def get_user_organizations(user) -> QuerySet[Organization]:
    """Return only active organizations the authenticated user belongs to."""
    if not user or not user.is_authenticated:
        return Organization.objects.none()
    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        is_active=True,
    ).distinct()


def get_organization_memberships(organization_id) -> QuerySet[OrganizationMembership]:
    if not organization_id:
        return OrganizationMembership.objects.none()
    return OrganizationMembership.objects.select_related("user", "role").filter(
        organization_id=organization_id,
        is_active=True,
    )

