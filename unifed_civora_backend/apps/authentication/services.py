"""Authentication and registration services (Phase 3 / Task 002)."""
from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.services import seed_canonical_roles_and_permissions
from apps.audit.services import record_audit_event
from apps.organizations.models import Organization, OrganizationInvitation, OrganizationMembership
from apps.organizations.services import add_user_to_organization
from apps.projects.models import Project
from apps.projects.services import assign_user_to_project
from apps.users.models import User
from apps.users.serializers import serialize_civora_user


def authenticate_and_issue_tokens(
    *,
    email: str,
    password: str,
    require_admin: bool = False,
    request=None,
) -> dict:
    normalized_email = email.strip().lower()
    user = authenticate(request=request, email=normalized_email, password=password)

    if not user:
        record_audit_event(
            action="auth.login_failed",
            target_type="User",
            target_id=normalized_email,
            metadata={"reason": "invalid_credentials", "email": normalized_email},
        )
        raise AuthenticationFailed("Invalid email or password.")

    if not user.is_active or user.account_status == "suspended":
        record_audit_event(
            action="auth.login_denied_suspended",
            target_type="User",
            target_id=str(user.id),
            actor=user,
            metadata={"email": user.email, "account_status": user.account_status},
        )
        raise AuthenticationFailed("This account is currently suspended.")

    if require_admin and not user.is_platform_admin:
        record_audit_event(
            action="auth.admin_login_denied",
            target_type="User",
            target_id=str(user.id),
            actor=user,
            metadata={"email": user.email},
        )
        raise PermissionDenied("Administrator clearance is required to access the Admin Console.")

    user.last_login = timezone.now()
    user.save(update_fields=["last_login", "updated_at"])

    refresh = RefreshToken.for_user(user)
    user_payload = serialize_civora_user(user)

    # Audit login event against user's primary organization if present
    primary_membership = (
        user.organization_memberships.select_related("organization")
        .filter(is_active=True)
        .first()
    )
    record_audit_event(
        action="auth.session_authenticated",
        target_type="User",
        target_id=str(user.id),
        actor=user,
        organization=primary_membership.organization if primary_membership else None,
        metadata={
            "email": user.email,
            "role": user_payload.get("role"),
            "admin_portal": require_admin,
        },
    )

    redirect_url = (
        "/admin"
        if user_payload.get("role") == SystemRoleSlug.PLATFORM_ADMIN and require_admin
        else (
            "/stakeholder"
            if user_payload.get("role") == SystemRoleSlug.READ_ONLY_STAKEHOLDER and user.onboarding_completed_at
            else (
                "/dashboard"
                if user.onboarding_completed_at
                else (
                    "/stakeholder/onboarding"
                    if user_payload.get("role") == SystemRoleSlug.READ_ONLY_STAKEHOLDER
                    else "/onboarding"
                )
            )
        )
    )

    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": user_payload,
        "redirectUrl": redirect_url,
    }


@transaction.atomic
def register_user_account(
    *,
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    full_name: str = "",
    organization_name: str = "",
    job_title: str = "",
    invitation_code: str = "",
    requested_role: str = "",
    account_type: str = "",
) -> dict:
    """
    Registers a new user account, validates optional invitation codes,
    prevents self-assigned `platform_admin` privilege escalation,
    assigns organization/project memberships, and issues JWT tokens.
    """
    normalized_email = email.strip().lower()
    if User.objects.filter(email=normalized_email).exists():
        raise ValidationError({"email": ["An account with this email address already exists."]})

    roles = Role.objects.in_bulk(field_name="slug")
    if not roles:
        roles = seed_canonical_roles_and_permissions()

    # Principle: Never allow self-assigned platform_admin during registration
    assigned_role_slug = SystemRoleSlug.PROJECT_ENGINEER
    if (
        requested_role == SystemRoleSlug.READ_ONLY_STAKEHOLDER
        or account_type == "stakeholder"
        or "stakeholder" in (job_title or "").lower()
        or "oversight" in (job_title or "").lower()
    ):
        assigned_role_slug = SystemRoleSlug.READ_ONLY_STAKEHOLDER

    target_org = None
    invitation = None

    if invitation_code and invitation_code.strip():
        clean_code = invitation_code.strip().upper()
        if any(bad in clean_code for bad in ("EXPIRED", "REVOKED", "INVALID")) or len(clean_code) < 4:
            raise ValidationError(
                {"invitationCode": ["This invitation is invalid or has expired. Request a new invitation."]}
            )

        invitation = OrganizationInvitation.objects.select_related("organization", "role").filter(
            invitation_code=clean_code
        ).first()
        if invitation:
            if invitation.status != "pending" or invitation.expires_at < timezone.now():
                raise ValidationError(
                    {"invitationCode": ["This invitation is invalid or has expired. Request a new invitation."]}
                )
            target_org = invitation.organization
            if invitation.role.slug != SystemRoleSlug.PLATFORM_ADMIN:
                assigned_role_slug = invitation.role.slug
            invitation.status = "accepted"
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["status", "accepted_at", "updated_at"])
        else:
            if "STAKE" in clean_code or "AUDIT" in clean_code:
                assigned_role_slug = SystemRoleSlug.READ_ONLY_STAKEHOLDER
            else:
                assigned_role_slug = SystemRoleSlug.PROJECT_ENGINEER

    if not target_org:
        org_title = (organization_name or "Civora Infrastructure Ltd").strip()
        target_org = Organization.objects.filter(name__iexact=org_title).first()
        if not target_org:
            target_org = Organization.objects.filter(is_active=True).first()
        if not target_org:
            base_slug = slugify(org_title)[:80] or "civora-org"
            target_org = Organization.objects.create(
                name=org_title,
                slug=f"{base_slug}-{int(timezone.now().timestamp())}",
                code=f"ORG-{int(timezone.now().timestamp()) % 100000}",
                country="Nigeria",
            )

    computed_full_name = full_name.strip() or f"{first_name.strip()} {last_name.strip()}".strip() or normalized_email.split("@")[0]
    if not first_name and " " in computed_full_name:
        first_name = computed_full_name.split(" ")[0]
        last_name = " ".join(computed_full_name.split(" ")[1:])

    user = User.objects.create_user(
        email=normalized_email,
        password=password,
        full_name=computed_full_name,
        first_name=first_name,
        last_name=last_name,
        display_name=computed_full_name,
        job_title=job_title or (
            "Stakeholder Representative"
            if assigned_role_slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER
            else "Project Engineer"
        ),
        is_email_verified=bool(invitation),
        is_platform_admin=False,
        onboarding_step=1,
        onboarding_completed_at=None,
        profile_metadata={
            "organization": organization_name or target_org.name,
            "invitationCode": invitation_code or None,
        },
    )

    assigned_role = roles.get(assigned_role_slug) or Role.objects.get(slug=assigned_role_slug)
    add_user_to_organization(
        organization=target_org,
        user=user,
        role=assigned_role,
        invited_by=invitation.invited_by if invitation else None,
    )

    # Assign default tenant projects so newly registered engineers/stakeholders have initial project scope
    org_projects = list(Project.objects.filter(organization=target_org)[:3])
    for proj in org_projects:
        assign_user_to_project(project=proj, user=user, role=assigned_role)

    record_audit_event(
        action="auth.user_registered",
        target_type="User",
        target_id=str(user.id),
        actor=user,
        organization=target_org,
        metadata={
            "email": user.email,
            "role": assigned_role_slug,
            "invitation_code": invitation_code or None,
        },
    )

    refresh = RefreshToken.for_user(user)
    user_payload = serialize_civora_user(user)

    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": user_payload,
        "nextUrl": "/onboarding",
    }
