"""Read selectors for Users domain."""
from typing import Optional
from django.db.models import QuerySet
from .models import User


def get_user_by_email(email: str) -> Optional[User]:
    return User.objects.filter(email__iexact=email.strip(), is_active=True).first()


def get_organization_users(organization_id) -> QuerySet[User]:
    """Return active users belonging to a specific tenant organization."""
    if not organization_id:
        return User.objects.none()
    return User.objects.filter(
        organization_memberships__organization_id=organization_id,
        organization_memberships__is_active=True,
        is_active=True,
    ).distinct()

