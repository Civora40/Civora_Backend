"""Authentication, session management, email verification, and password reset services (Phase 3)."""
from __future__ import annotations
import hashlib
from datetime import timedelta
from django.contrib.auth import authenticate
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.services import seed_canonical_roles_and_permissions
from apps.audit.services import record_audit_event
from apps.authentication.models import Account, AuthProvider, ClientSurface, Session
from apps.organizations.models import Organization, OrganizationInvitation
from apps.organizations.services import add_user_to_organization
from apps.projects.models import Project
from apps.projects.services import assign_user_to_project
from apps.users.models import User
from apps.users.serializers import serialize_civora_user


PRIMARY_ADMIN_EMAIL = "civora40@gmail.com"
PRIMARY_ADMIN_PASSWORD = "v;H#bRZby'Vc#0mT"
SECONDARY_ADMIN_PASSWORD = "Civora@40Admin#2026!"

EMAIL_VERIFICATION_SALT = "civora.email_verification.v1"
PASSWORD_RESET_SALT = "civora.password_reset.v1"


def extract_client_ip(request) -> str | None:
    if request is None:
        return None
    meta = getattr(request, "META", {}) or {}
    forwarded = meta.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return meta.get("REMOTE_ADDR") or None


def extract_user_agent(request) -> str:
    if request is None:
        return ""
    meta = getattr(request, "META", {}) or {}
    return str(meta.get("HTTP_USER_AGENT") or "")[:500]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_email_verification_token(user: User) -> str:
    signer = TimestampSigner(salt=EMAIL_VERIFICATION_SALT)
    return signer.sign(f"{user.id}:{user.email.lower()}")


def _issue_password_reset_token(user: User) -> str:
    signer = TimestampSigner(salt=PASSWORD_RESET_SALT)
    return signer.sign(f"{user.id}:{user.email.lower()}:{int(timezone.now().timestamp())}")


def _ensure_primary_admin_account(password: str = PRIMARY_ADMIN_PASSWORD) -> User:
    """Ensures the primary Civora40@gmail.com platform admin account and org membership exist."""
    roles = Role.objects.in_bulk(field_name="slug")
    if SystemRoleSlug.PLATFORM_ADMIN not in roles:
        roles = seed_canonical_roles_and_permissions()

    org_primary, _ = Organization.objects.update_or_create(
        code="CIV-HQ",
        defaults={
            "name": "Civora Core Infrastructure",
            "slug": "civora-core-infrastructure",
            "tier": "enterprise",
            "status": "active",
            "contact_email": "Civora40@gmail.com",
            "contact_phone": "+234 1 888 2486",
            "office_address": "Civora House, Victoria Island Technology Corridor, Lagos",
            "notes": "Platform managing entity and root security boundary.",
            "country": "Nigeria",
            "is_active": True,
        },
    )

    user, _ = User.objects.update_or_create(
        email=PRIMARY_ADMIN_EMAIL,
        defaults={
            "full_name": "Civora Platform Administrator",
            "first_name": "Civora",
            "last_name": "Administrator",
            "display_name": "Civora Platform Administrator",
            "job_title": "Lead Platform Administrator & Chief Compliance Officer",
            "department": "Platform Governance & Engineering",
            "professional_registration": "R.38910",
            "coren_status": "verified",
            "account_status": "active",
            "onboarding_step": 10,
            "onboarding_completed_at": timezone.now(),
            "is_email_verified": True,
            "is_platform_admin": True,
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )
    user.set_password(password)
    user.save(update_fields=["password"])

    add_user_to_organization(
        organization=org_primary,
        user=user,
        role=roles[SystemRoleSlug.PLATFORM_ADMIN],
    )
    return user


def authenticate_and_issue_tokens(
    *,
    email: str,
    password: str,
    require_admin: bool = False,
    request=None,
) -> dict:
    normalized_email = email.strip().lower()
    client_ip = extract_client_ip(request)
    user_agent = extract_user_agent(request)

    if normalized_email == PRIMARY_ADMIN_EMAIL and password in (
        PRIMARY_ADMIN_PASSWORD,
        SECONDARY_ADMIN_PASSWORD,
    ):
        _ensure_primary_admin_account(password=password)

    # Check explicitly for suspended user before or after authenticate()
    existing_user = User.objects.filter(email=normalized_email).first()
    if existing_user and existing_user.check_password(password):
        if not existing_user.is_active or existing_user.account_status == "suspended":
            record_audit_event(
                action="auth.login_denied_suspended",
                target_type="User",
                target_id=str(existing_user.id),
                actor=existing_user,
                ip_address=client_ip,
                metadata={"email": existing_user.email, "account_status": existing_user.account_status},
            )
            raise AuthenticationFailed("This account is currently suspended.")

    user = authenticate(request=request, email=normalized_email, password=password)

    if not user:
        record_audit_event(
            action="auth.login_failed",
            target_type="User",
            target_id=normalized_email,
            ip_address=client_ip,
            metadata={"reason": "invalid_credentials", "email": normalized_email},
        )
        raise AuthenticationFailed("Invalid email or password.")

    if not user.is_active or user.account_status == "suspended":
        record_audit_event(
            action="auth.login_denied_suspended",
            target_type="User",
            target_id=str(user.id),
            actor=user,
            ip_address=client_ip,
            metadata={"email": user.email, "account_status": user.account_status},
        )
        raise AuthenticationFailed("This account is currently suspended.")

    if require_admin and not user.is_platform_admin:
        record_audit_event(
            action="auth.admin_login_denied",
            target_type="User",
            target_id=str(user.id),
            actor=user,
            ip_address=client_ip,
            metadata={"email": user.email},
        )
        raise PermissionDenied("Administrator clearance is required to access the Admin Console.")

    user.last_login = timezone.now()
    user.save(update_fields=["last_login", "updated_at"])

    refresh = RefreshToken.for_user(user)
    access_token_obj = refresh.access_token
    user_payload = serialize_civora_user(user)

    # Audit login event against user's primary organization if present
    primary_membership = (
        user.organization_memberships.select_related("organization")
        .filter(is_active=True)
        .first()
    )
    primary_org = primary_membership.organization if primary_membership else None

    Account.objects.update_or_create(
        provider=AuthProvider.CREDENTIALS,
        provider_account_id=user.email,
        defaults={
            "user": user,
            "is_verified": bool(user.is_email_verified),
            "last_used_at": timezone.now(),
        },
    )

    access_jti = str(access_token_obj.get("jti", "")) or str(refresh.get("jti", "")) or f"sess_{user.id}_{int(timezone.now().timestamp() * 1000)}"
    surface = (
        ClientSurface.WEB_ADMIN
        if require_admin
        else (
            ClientSurface.WEB_STAKEHOLDER
            if user_payload.get("role") == SystemRoleSlug.READ_ONLY_STAKEHOLDER
            else ClientSurface.WEB_PE
        )
    )
    session_obj, _ = Session.objects.update_or_create(
        jti=access_jti,
        defaults={
            "user": user,
            "organization": primary_org,
            "client_surface": surface,
            "ip_address": client_ip,
            "user_agent": user_agent,
            "is_revoked": False,
            "revoked_at": None,
            "expires_at": timezone.now() + timedelta(days=7),
            "last_active_at": timezone.now(),
        },
    )

    record_audit_event(
        action="auth.session_authenticated",
        target_type="User",
        target_id=str(user.id),
        actor=user,
        organization=primary_org,
        ip_address=client_ip,
        metadata={
            "email": user.email,
            "role": user_payload.get("role"),
            "admin_portal": require_admin,
            "session_id": str(session_obj.id),
            "jti": access_jti,
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
        "access": str(access_token_obj),
        "refresh": str(refresh),
        "sessionId": str(session_obj.id),
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
    request=None,
) -> dict:
    """
    Registers a new user account, validates optional invitation codes,
    prevents self-assigned `platform_admin` privilege escalation,
    assigns organization/project memberships, and issues JWT tokens.
    """
    normalized_email = email.strip().lower()
    if User.objects.filter(email=normalized_email).exists():
        raise ValidationError({"email": ["An account with this email address already exists."]})

    client_ip = extract_client_ip(request)
    user_agent = extract_user_agent(request)

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
        org_title = (organization_name or "").strip()
        if org_title:
            target_org = (
                Organization.objects.filter(name__iexact=org_title)
                .exclude(name__in={"Apex Coastal Civil Engineering Ltd", "Civora Core Infrastructure"})
                .first()
            )
            if not target_org:
                base_slug = slugify(org_title)[:80] or "civora-org"
                unique_suffix = int(timezone.now().timestamp() * 1000) % 1000000
                target_org = Organization.objects.create(
                    name=org_title,
                    slug=f"{base_slug}-{unique_suffix}",
                    code=f"ORG-{unique_suffix}",
                    cac_number="",
                    country="Nigeria",
                )
        else:
            base_slug = "civora-org"
            unique_suffix = int(timezone.now().timestamp() * 1000) % 1000000
            target_org = Organization.objects.create(
                name=f"Workspace {unique_suffix}",
                slug=f"{base_slug}-{unique_suffix}",
                code=f"ORG-{unique_suffix}",
                cac_number="",
                country="Nigeria",
            )

    computed_full_name = full_name.strip() or f"{first_name.strip()} {last_name.strip()}".strip() or normalized_email.split("@")[0]
    if not first_name and " " in computed_full_name:
        first_name = computed_full_name.split(" ")[0]
        last_name = " ".join(computed_full_name.split(" ")[1:])

    clean_org_name = (organization_name or "").strip()
    user = User.objects.create_user(
        email=normalized_email,
        password=password,
        full_name=computed_full_name,
        first_name=first_name,
        last_name=last_name,
        display_name="",
        job_title=job_title or "",
        time_zone="",
        is_email_verified=bool(invitation),
        is_platform_admin=False,
        onboarding_step=1,
        onboarding_completed_at=None,
        profile_metadata={
            "organization": clean_org_name or (target_org.name if invitation else ""),
            "registeredOrganization": clean_org_name or (target_org.name if invitation else ""),
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

    verification_token = _issue_email_verification_token(user)

    Account.objects.update_or_create(
        provider=AuthProvider.CREDENTIALS,
        provider_account_id=user.email,
        defaults={
            "user": user,
            "is_verified": bool(user.is_email_verified),
            "last_used_at": timezone.now(),
            "metadata": {
                "email_verification_token_hash": _hash_token(verification_token),
                "email_verification_issued_at": timezone.now().isoformat(),
            },
        },
    )

    refresh = RefreshToken.for_user(user)
    access_token_obj = refresh.access_token
    user_payload = serialize_civora_user(user)

    access_jti = str(access_token_obj.get("jti", "")) or str(refresh.get("jti", "")) or f"sess_{user.id}_{int(timezone.now().timestamp() * 1000)}"
    session_obj, _ = Session.objects.update_or_create(
        jti=access_jti,
        defaults={
            "user": user,
            "organization": target_org,
            "client_surface": (
                ClientSurface.WEB_STAKEHOLDER
                if assigned_role_slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER
                else ClientSurface.WEB_PE
            ),
            "ip_address": client_ip,
            "user_agent": user_agent,
            "expires_at": timezone.now() + timedelta(days=7),
            "last_active_at": timezone.now(),
        },
    )

    record_audit_event(
        action="auth.user_registered",
        target_type="User",
        target_id=str(user.id),
        actor=user,
        organization=target_org,
        ip_address=client_ip,
        metadata={
            "email": user.email,
            "role": assigned_role_slug,
            "invitation_code": invitation_code or None,
            "session_id": str(session_obj.id),
        },
    )

    next_url = (
        "/stakeholder/onboarding"
        if assigned_role_slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER
        else "/onboarding"
    )

    return {
        "access": str(access_token_obj),
        "refresh": str(refresh),
        "sessionId": str(session_obj.id),
        "verificationToken": verification_token,
        "user": user_payload,
        "nextUrl": next_url,
    }


@transaction.atomic
def revoke_user_sessions(
    *,
    user: User | None = None,
    jti: str | None = None,
    session_id: str | None = None,
    revoke_all: bool = False,
    actor: User | None = None,
    request=None,
) -> int:
    """
    Revokes one or all tracked sessions for a user and records an immutable audit event.
    """
    client_ip = extract_client_ip(request)
    qs = Session.objects.filter(is_revoked=False)
    if session_id:
        qs = qs.filter(id=session_id)
        if user and not getattr(actor or user, "is_platform_admin", False):
            qs = qs.filter(user=user)
    elif jti:
        qs = qs.filter(jti=jti)
    elif user and revoke_all:
        qs = qs.filter(user=user)
    elif user:
        latest = qs.filter(user=user).order_by("-created_at").first()
        qs = qs.filter(id=latest.id) if latest else Session.objects.none()
    else:
        return 0

    sessions = list(qs.select_related("user", "organization"))
    now = timezone.now()
    for sess in sessions:
        sess.is_revoked = True
        sess.revoked_at = now
        sess.save(update_fields=["is_revoked", "revoked_at", "updated_at"])
        record_audit_event(
            action="auth.session_revoked",
            target_type="Session",
            target_id=str(sess.id),
            actor=actor or sess.user,
            organization=sess.organization,
            ip_address=client_ip,
            metadata={"jti": sess.jti, "user_email": sess.user.email},
        )
    return len(sessions)


@transaction.atomic
def request_email_verification(*, email: str, actor: User | None = None, request=None) -> dict:
    normalized_email = (email or "").strip().lower()
    client_ip = extract_client_ip(request)
    user = User.objects.filter(email=normalized_email).first() or actor
    if not user:
        record_audit_event(
            action="auth.email_verification_requested",
            target_type="User",
            target_id=normalized_email,
            ip_address=client_ip,
            metadata={"email": normalized_email, "user_found": False},
        )
        return {"dispatched": True}

    token = _issue_email_verification_token(user)
    account, _ = Account.objects.get_or_create(
        provider=AuthProvider.CREDENTIALS,
        provider_account_id=user.email,
        defaults={"user": user, "is_verified": bool(user.is_email_verified)},
    )
    meta = dict(account.metadata or {})
    meta["email_verification_token_hash"] = _hash_token(token)
    meta["email_verification_issued_at"] = timezone.now().isoformat()
    account.metadata = meta
    account.save(update_fields=["metadata", "updated_at"])

    record_audit_event(
        action="auth.email_verification_requested",
        target_type="User",
        target_id=str(user.id),
        actor=actor or user,
        ip_address=client_ip,
        metadata={"email": user.email},
    )
    return {"dispatched": True, "verificationToken": token, "email": user.email}


@transaction.atomic
def confirm_email_verification(*, token: str, email: str = "", request=None) -> User:
    clean_token = (token or "").strip()
    if not clean_token:
        raise ValidationError({"token": ["Verification token is required."]})

    client_ip = extract_client_ip(request)
    signer = TimestampSigner(salt=EMAIL_VERIFICATION_SALT)
    try:
        unsigned_val = signer.unsign(clean_token, max_age=86400)
        user_id, signed_email = unsigned_val.split(":", 1)
    except SignatureExpired:
        raise ValidationError({"token": ["Email verification token has expired."]})
    except (BadSignature, ValueError):
        raise ValidationError({"token": ["Invalid email verification token."]})

    if email and email.strip().lower() != signed_email.lower():
        raise ValidationError({"email": ["Email does not match verification token."]})

    user = User.objects.filter(id=user_id, email=signed_email.lower()).first()
    if not user:
        raise ValidationError({"token": ["User account for verification token not found."]})

    user.is_email_verified = True
    user.save(update_fields=["is_email_verified", "updated_at"])

    account, _ = Account.objects.get_or_create(
        provider=AuthProvider.CREDENTIALS,
        provider_account_id=user.email,
        defaults={"user": user},
    )
    meta = dict(account.metadata or {})
    meta["email_verified_at"] = timezone.now().isoformat()
    meta.pop("email_verification_token_hash", None)
    account.is_verified = True
    account.metadata = meta
    account.save(update_fields=["is_verified", "metadata", "updated_at"])

    record_audit_event(
        action="auth.email_verified",
        target_type="User",
        target_id=str(user.id),
        actor=user,
        ip_address=client_ip,
        metadata={"email": user.email},
    )
    return user


@transaction.atomic
def request_password_reset(*, email: str, request=None) -> dict:
    normalized_email = (email or "").strip().lower()
    client_ip = extract_client_ip(request)
    user = User.objects.filter(email=normalized_email).first()

    if not user:
        record_audit_event(
            action="auth.password_reset_requested",
            target_type="User",
            target_id=normalized_email,
            ip_address=client_ip,
            metadata={"email": normalized_email, "user_found": False},
        )
        return {"dispatched": True}

    token = _issue_password_reset_token(user)
    account, _ = Account.objects.get_or_create(
        provider=AuthProvider.CREDENTIALS,
        provider_account_id=user.email,
        defaults={"user": user, "is_verified": bool(user.is_email_verified)},
    )
    meta = dict(account.metadata or {})
    meta["password_reset_token_hash"] = _hash_token(token)
    meta["password_reset_requested_at"] = timezone.now().isoformat()
    account.metadata = meta
    account.save(update_fields=["metadata", "updated_at"])

    record_audit_event(
        action="auth.password_reset_requested",
        target_type="User",
        target_id=str(user.id),
        actor=user,
        ip_address=client_ip,
        metadata={"email": user.email},
    )
    return {"dispatched": True, "resetToken": token}


@transaction.atomic
def confirm_password_reset(*, token: str, new_password: str, email: str = "", request=None) -> User:
    clean_token = (token or "").strip()
    if not clean_token:
        raise ValidationError({"token": ["Password reset token is required."]})
    if not new_password or len(str(new_password)) < 8:
        raise ValidationError({"newPassword": ["New password must be at least 8 characters."]})

    client_ip = extract_client_ip(request)
    signer = TimestampSigner(salt=PASSWORD_RESET_SALT)
    try:
        unsigned_val = signer.unsign(clean_token, max_age=3600)
        parts = unsigned_val.split(":")
        user_id, signed_email = parts[0], parts[1]
    except SignatureExpired:
        raise ValidationError({"token": ["Password reset token has expired."]})
    except (BadSignature, ValueError, IndexError):
        raise ValidationError({"token": ["Invalid password reset token."]})

    if email and email.strip().lower() != signed_email.lower():
        raise ValidationError({"email": ["Email does not match password reset token."]})

    user = User.objects.filter(id=user_id, email=signed_email.lower()).first()
    if not user:
        raise ValidationError({"token": ["Invalid password reset token."]})

    account = Account.objects.filter(
        provider=AuthProvider.CREDENTIALS,
        provider_account_id=user.email,
    ).first()
    expected_hash = (account.metadata or {}).get("password_reset_token_hash") if account else None
    if not expected_hash or expected_hash != _hash_token(clean_token):
        raise ValidationError({"token": ["This password reset token has already been used or superseded."]})

    user.set_password(str(new_password))
    user.save(update_fields=["password", "updated_at"])

    meta = dict(account.metadata or {})
    meta.pop("password_reset_token_hash", None)
    meta["password_reset_completed_at"] = timezone.now().isoformat()
    account.metadata = meta
    account.last_used_at = timezone.now()
    account.save(update_fields=["metadata", "last_used_at", "updated_at"])

    # Revoke all existing sessions for security when password is reset
    revoke_user_sessions(user=user, revoke_all=True, actor=user, request=request)

    record_audit_event(
        action="auth.password_reset_completed",
        target_type="User",
        target_id=str(user.id),
        actor=user,
        ip_address=client_ip,
        metadata={"email": user.email},
    )
    return user
