"""Write services for Organizations domain."""
from django.db import transaction
from apps.access_control.models import Role
from .models import Organization, OrganizationMembership


@transaction.atomic
def create_organization(
    *,
    name: str,
    slug: str,
    code: str,
    country: str = "Nigeria",
    compliance_threshold_percent: float = 80.00,
) -> Organization:
    return Organization.objects.create(
        name=name,
        slug=slug,
        code=code.upper(),
        country=country,
        compliance_threshold_percent=compliance_threshold_percent,
    )


@transaction.atomic
def add_user_to_organization(
    *,
    organization: Organization,
    user,
    role: Role,
    invited_by=None,
) -> OrganizationMembership:
    membership, _ = OrganizationMembership.objects.update_or_create(
        organization=organization,
        user=user,
        defaults={
            "role": role,
            "invited_by": invited_by,
            "is_active": True,
        },
    )
    return membership

