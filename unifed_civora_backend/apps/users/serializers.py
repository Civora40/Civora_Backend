"""Serializers for user profiles and frontend CivoraUser contract."""
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers
from apps.access_control.models import Role, SystemRoleSlug
from apps.organizations.models import Organization, OrganizationMembership
from apps.projects.models import ProjectMembership
from .models import User

SEED_ORG_NAMES = {
    "Apex Coastal Civil Engineering Ltd",
    "Civora Core Infrastructure",
    "Civora Infrastructure Ltd",
    "Apex Infrastructure Nigeria Ltd",
}

SEED_CAC_NUMBERS = {
    "RC-45190",
    "RC-1928374",
    "RC-11234",
    "RC-8841023",
    "RC 1849204",
}

SEED_EMAIL_DOMAINS = (
    "@civora.internal",
    "@civora.io",
    "@apexcivil.com",
    "@juliusberger.com",
)


def is_seeded_demo_user(user: User) -> bool:
    email = (user.email or "").strip().lower()
    return email.endswith(SEED_EMAIL_DOMAINS)


def sync_user_registered_organization(user: User) -> None:
    """
    Ensures a real (non-seeded) user's active OrganizationMembership matches the
    company name they registered with or updated during onboarding, and strips any
    accidental linkage to seeded demo organizations (e.g. Apex Coastal Civil Engineering Ltd).
    """
    if is_seeded_demo_user(user):
        return

    meta = dict(user.profile_metadata or {})
    meta_changed = False

    raw_cac = str(meta.get("cacNumber") or "").strip()
    if raw_cac in SEED_CAC_NUMBERS:
        meta.pop("cacNumber", None)
        raw_cac = ""
        meta_changed = True

    desired_org_name = str(
        meta.get("registeredOrganization") or meta.get("organization") or ""
    ).strip()

    if (
        meta.get("_orgSynced") is True
        and desired_org_name
        and desired_org_name not in SEED_ORG_NAMES
        and meta.get("_syncedOrgName") == desired_org_name
        and meta.get("_syncedCac") == raw_cac
    ):
        return

    if desired_org_name in SEED_ORG_NAMES:
        desired_org_name = ""
        if meta.get("organization") in SEED_ORG_NAMES:
            meta["organization"] = ""
            meta_changed = True

    if desired_org_name and meta.get("registeredOrganization") != desired_org_name:
        meta["registeredOrganization"] = desired_org_name
        meta["organization"] = desired_org_name
        meta_changed = True

    if desired_org_name and (
        meta.get("_orgSynced") is not True
        or meta.get("_syncedOrgName") != desired_org_name
        or meta.get("_syncedCac") != raw_cac
    ):
        meta["_orgSynced"] = True
        meta["_syncedOrgName"] = desired_org_name
        meta["_syncedCac"] = raw_cac
        meta_changed = True

    if meta_changed:
        user.profile_metadata = meta
        user.save(update_fields=["profile_metadata", "updated_at"])

    active_memberships = list(
        user.organization_memberships.select_related("organization", "role").filter(is_active=True)
    )
    active_org_membership = active_memberships[0] if active_memberships else None

    if desired_org_name:
        current_org = active_org_membership.organization if active_org_membership else None
        needs_rebind = (
            not current_org
            or current_org.name in SEED_ORG_NAMES
            or current_org.name.strip().lower() != desired_org_name.lower()
        )
        if needs_rebind:
            # If the current organization belongs solely to this user and is not a seeded org, rename it
            if (
                current_org
                and current_org.name not in SEED_ORG_NAMES
                and OrganizationMembership.objects.filter(organization=current_org).count() <= 1
            ):
                current_org.name = desired_org_name
                if raw_cac:
                    current_org.cac_number = raw_cac[:64]
                current_org.save(update_fields=["name", "cac_number", "updated_at"])
            else:
                target_org = (
                    Organization.objects.filter(name__iexact=desired_org_name)
                    .exclude(name__in=SEED_ORG_NAMES)
                    .first()
                )
                if not target_org:
                    base_slug = slugify(desired_org_name)[:80] or "civora-org"
                    unique_suffix = int(timezone.now().timestamp() * 1000) % 1000000
                    target_org = Organization.objects.create(
                        name=desired_org_name,
                        slug=f"{base_slug}-{unique_suffix}",
                        code=f"ORG-{unique_suffix}",
                        cac_number=raw_cac[:64] if raw_cac else "",
                        country="Nigeria",
                    )
                elif raw_cac and target_org.cac_number != raw_cac:
                    target_org.cac_number = raw_cac[:64]
                    target_org.save(update_fields=["cac_number", "updated_at"])

                role = (
                    active_org_membership.role
                    if active_org_membership and active_org_membership.role
                    else Role.objects.filter(slug=SystemRoleSlug.PROJECT_ENGINEER).first()
                )
                if role:
                    OrganizationMembership.objects.update_or_create(
                        organization=target_org,
                        user=user,
                        defaults={"role": role, "is_active": True},
                    )
                # Remove accidental memberships in seeded organizations & seeded projects
                OrganizationMembership.objects.filter(
                    user=user, organization__name__in=SEED_ORG_NAMES
                ).delete()
                ProjectMembership.objects.filter(
                    user=user, project__organization__name__in=SEED_ORG_NAMES
                ).delete()
        elif current_org and raw_cac and current_org.cac_number != raw_cac:
            current_org.cac_number = raw_cac[:64]
            current_org.save(update_fields=["cac_number", "updated_at"])
    else:
        # Even if desired_org_name is blank, detach real user from Apex Coastal Civil Engineering Ltd
        OrganizationMembership.objects.filter(
            user=user, organization__name="Apex Coastal Civil Engineering Ltd"
        ).delete()
        ProjectMembership.objects.filter(
            user=user, project__organization__name="Apex Coastal Civil Engineering Ltd"
        ).delete()


def serialize_civora_user(user: User) -> dict:
    """
    Serializes a Django `User` into a unified payload that includes both
    snake_case backend fields and camelCase `CivoraUser` frontend fields
    expected by `Civora Frontend` and `Civora_Admin`.
    """
    sync_user_registered_organization(user)

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

    meta = dict(user.profile_metadata or {})
    seed_user = is_seeded_demo_user(user)

    meta_org = str(meta.get("registeredOrganization") or meta.get("organization") or "").strip()
    membership_org = (
        active_org_membership.organization.name
        if active_org_membership and active_org_membership.organization
        else ""
    )

    if seed_user:
        org_name = membership_org or meta_org or "Civora Core Infrastructure"
    else:
        if meta_org and meta_org not in SEED_ORG_NAMES:
            org_name = meta_org
        elif membership_org and membership_org not in SEED_ORG_NAMES:
            org_name = membership_org
        else:
            org_name = ""

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

    raw_org_cac = (
        active_org_membership.organization.cac_number
        if active_org_membership and active_org_membership.organization and active_org_membership.organization.cac_number
        else ""
    )
    raw_meta_cac = str(meta.get("cacNumber") or "").strip()
    if seed_user:
        resolved_cac = raw_org_cac or raw_meta_cac
    else:
        if raw_meta_cac and raw_meta_cac not in SEED_CAC_NUMBERS:
            resolved_cac = raw_meta_cac
        elif raw_org_cac and raw_org_cac not in SEED_CAC_NUMBERS:
            resolved_cac = raw_org_cac
        else:
            resolved_cac = ""

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
        "registeredOrganization": org_name,
        "organizationId": org_id,
        "phone": user.phone_number or raw_phone_display or "",
        "timeZone": user.time_zone or "",
        "corenNumber": user.professional_registration or "",
        "corenStatus": canonical_coren_status,
        "corenCategory": coren_category or "",
        "cacNumber": resolved_cac,
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
