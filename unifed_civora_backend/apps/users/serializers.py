"""Serializers for user profiles and frontend CivoraUser contract."""
from rest_framework import serializers
from apps.access_control.models import SystemRoleSlug
from .models import User


def serialize_civora_user(user: User) -> dict:
    """
    Serializes a Django `User` into a unified payload that includes both
    snake_case backend fields and camelCase `CivoraUser` frontend fields
    expected by `Civora Frontend` and `Civora_Admin`.
    """
    active_org_membership = (
        user.organization_memberships.select_related("organization", "role")
        .filter(is_active=True, organization__is_active=True)
        .first()
    )
    active_proj_memberships = list(
        user.project_memberships.select_related("project", "role")
        .filter(is_active=True)
    )

    if user.is_platform_admin:
        role_slug = SystemRoleSlug.PLATFORM_ADMIN
        assigned_projects = ["*"]
    elif active_org_membership and active_org_membership.role:
        role_slug = active_org_membership.role.slug
        if role_slug in (SystemRoleSlug.PLATFORM_ADMIN, SystemRoleSlug.ORGANIZATION_ADMIN):
            role_slug = SystemRoleSlug.PLATFORM_ADMIN if user.is_platform_admin else "project_engineer"
            assigned_projects = ["*"]
        elif role_slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER:
            assigned_projects = [str(m.project_id) for m in active_proj_memberships]
        else:
            role_slug = "project_engineer"
            assigned_projects = [str(m.project_id) for m in active_proj_memberships]
    else:
        role_slug = "project_engineer"
        assigned_projects = [str(m.project_id) for m in active_proj_memberships]

    # Also include project codes for compatibility with existing frontend route checks
    project_codes = [m.project.code for m in active_proj_memberships if m.project]

    org_name = (
        active_org_membership.organization.name
        if active_org_membership and active_org_membership.organization
        else (user.profile_metadata.get("organization") or "Civora Infrastructure Ltd")
    )
    org_id = (
        str(active_org_membership.organization_id)
        if active_org_membership
        else None
    )

    first_name = user.first_name or (user.full_name.split(" ")[0] if user.full_name else "")
    last_name = user.last_name or (
        " ".join(user.full_name.split(" ")[1:]) if user.full_name and " " in user.full_name else ""
    )
    onboarding_ms = (
        int(user.onboarding_completed_at.timestamp() * 1000)
        if user.onboarding_completed_at
        else None
    )

    meta = dict(user.profile_metadata or {})
    # Prevent raw metadata keys from clobbering canonical status fields
    coren_category = meta.pop("corenCategory", None)
    raw_meta_coren_status = meta.pop("corenStatus", None)
    if not coren_category and raw_meta_coren_status and raw_meta_coren_status not in ("verified", "pending", "unregistered"):
        coren_category = raw_meta_coren_status
    raw_phone_display = meta.pop("phone", None)

    canonical_coren_status = (
        user.coren_status
        if user.coren_status in ("verified", "pending", "unregistered")
        else ("verified" if user.professional_registration else "unregistered")
    )

    return {
        **meta,
        # Backend canonical snake_case fields
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "first_name": first_name,
        "last_name": last_name,
        "display_name": user.display_name or user.full_name,
        "job_title": user.job_title,
        "department": user.department,
        "professional_registration": user.professional_registration,
        "coren_status": canonical_coren_status,
        "phone_number": user.phone_number,
        "time_zone": user.time_zone,
        "account_status": user.account_status if user.is_active else "suspended",
        "onboarding_step": user.onboarding_step,
        "onboarding_completed_at": user.onboarding_completed_at.isoformat() if user.onboarding_completed_at else None,
        "is_email_verified": user.is_email_verified,
        "is_phone_verified": user.is_phone_verified,
        "is_platform_admin": user.is_platform_admin,
        "organization_id": org_id,
        "organization_name": org_name,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        # Frontend CivoraUser camelCase contract fields
        "name": user.full_name,
        "firstName": first_name,
        "lastName": last_name,
        "displayName": user.display_name or "",
        "role": role_slug,
        "title": user.job_title or ("Platform Administrator" if role_slug == "platform_admin" else "Project Engineer"),
        "jobTitle": user.job_title or "",
        "organization": org_name,
        "organizationId": org_id,
        "phone": user.phone_number or raw_phone_display or "",
        "timeZone": user.time_zone or "",
        "corenNumber": user.professional_registration or "",
        "corenStatus": canonical_coren_status,
        "corenCategory": coren_category or "",
        "cacNumber": (
            active_org_membership.organization.cac_number
            if active_org_membership and active_org_membership.organization and active_org_membership.organization.cac_number
            else meta.get("cacNumber", "")
        ),
        "accountStatus": user.account_status if user.is_active else "suspended",
        "assignedProjects": assigned_projects if assigned_projects else project_codes,
        "assignedProjectCodes": project_codes,
        "onboardingStep": user.onboarding_step,
        "onboardingCompletedAt": onboarding_ms,
        "lastLoginAt": user.last_login.isoformat() if user.last_login else None,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
    }


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "full_name",
            "first_name",
            "last_name",
            "display_name",
            "job_title",
            "department",
            "professional_registration",
            "coren_status",
            "phone_number",
            "time_zone",
            "account_status",
            "onboarding_step",
            "onboarding_completed_at",
            "is_email_verified",
            "is_phone_verified",
            "is_platform_admin",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "email",
            "is_email_verified",
            "is_phone_verified",
            "is_platform_admin",
            "created_at",
        ]

    def to_representation(self, instance):
        return serialize_civora_user(instance)
