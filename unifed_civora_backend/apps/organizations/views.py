"""HTTP views for Organizations, Memberships, Role Assignment, and Invitations (Phase 3 & 4)."""
import random
from datetime import timedelta
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.services import enforce_organization_permission
from apps.audit.services import record_audit_event
from apps.users.models import User
from common.middleware.tenant import resolve_request_organization
from common.responses import api_success
from .models import Organization, OrganizationInvitation, OrganizationMembership
from .selectors import get_user_organizations
from .serializers import (
    OrganizationSerializer,
    serialize_invitation,
    serialize_membership_record,
    serialize_organization,
)
from .services import add_user_to_organization

CreateOrganizationRequestSerializer = inline_serializer(
    name="CreateOrganizationRequest",
    fields={
        "name": serializers.CharField(help_text="Organization legal or display name"),
        "code": serializers.CharField(help_text="Unique short code (e.g. CIV-FMWH-02)"),
        "tier": serializers.ChoiceField(
            choices=["government", "enterprise", "contractor"],
            required=False,
            default="enterprise",
        ),
        "status": serializers.ChoiceField(
            choices=["active", "suspended", "pending_verification"],
            required=False,
            default="active",
        ),
        "cacNumber": serializers.CharField(required=False, allow_blank=True),
        "contactEmail": serializers.EmailField(required=False, allow_blank=True),
        "contactPhone": serializers.CharField(required=False, allow_blank=True),
        "officeAddress": serializers.CharField(required=False, allow_blank=True),
        "notes": serializers.CharField(required=False, allow_blank=True),
    },
)

CreateInvitationRequestSerializer = inline_serializer(
    name="CreateInvitationRequest",
    fields={
        "email": serializers.EmailField(help_text="Recipient engineer or stakeholder email"),
        "role": serializers.CharField(
            required=False,
            default="project_engineer",
            help_text="System role slug (e.g. project_engineer, supervising_reviewer, read_only_stakeholder)",
        ),
        "organizationId": serializers.UUIDField(required=False),
        "assignedProjects": serializers.ListField(
            child=serializers.CharField(),
            required=False,
            help_text="List of project codes to pre-assign",
        ),
    },
)


class OrganizationListView(APIView):
    """GET, POST /api/v1/organizations/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="List organizations",
        description="Lists all organizations for Platform Admins, or the current user's active organizations.",
        responses={200: OrganizationSerializer(many=True)},
    )
    def get(self, request):
        if request.user.is_platform_admin:
            orgs = Organization.objects.all()
        else:
            orgs = get_user_organizations(request.user)
        serializer = OrganizationSerializer(orgs, many=True)
        return api_success(data=serializer.data)

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Provision a new organization (Platform Admin)",
        description="Creates a new tenant organization and records an audit event.",
        request=CreateOrganizationRequestSerializer,
        responses={201: OrganizationSerializer},
        examples=[
            OpenApiExample(
                "Provision Organization Example",
                value={
                    "name": "Reynolds Construction Company (RCC) Nigeria",
                    "code": "CIV-RCC-04",
                    "tier": "contractor",
                    "cacNumber": "RC-198842",
                    "contactEmail": "engineering@rcc.ng",
                    "contactPhone": "+234 809 111 2233",
                    "officeAddress": "Plot 1408, Mabushi District, Abuja FCT",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        if not request.user.is_platform_admin:
            record_audit_event(
                action="rbac.permission_denied",
                target_type="Organization",
                target_id="new",
                actor=request.user,
                metadata={"required_permission": "platform_admin"},
            )
            raise PermissionDenied("Only platform administrators can provision new organizations.")

        payload = request.data or {}
        name = (payload.get("name") or "").strip()
        code = (payload.get("code") or "").strip().upper()
        if not name or not code:
            raise ValidationError({"name": ["Organization name and code are required."]})

        if Organization.objects.filter(code=code).exists():
            raise ValidationError({"code": [f"Organization code '{code}' already exists."]})

        slug = slugify(payload.get("slug") or name)[:110]
        org = Organization.objects.create(
            name=name,
            slug=slug,
            code=code,
            tier=payload.get("tier") or "enterprise",
            status=payload.get("status") or "active",
            cac_number=payload.get("cacNumber") or payload.get("cac_number") or "",
            contact_email=payload.get("contactEmail") or payload.get("contact_email") or "",
            contact_phone=payload.get("contactPhone") or payload.get("contact_phone") or "",
            office_address=payload.get("officeAddress") or payload.get("office_address") or "",
            notes=payload.get("notes") or "",
            country=payload.get("country") or "Nigeria",
            compliance_threshold_percent=payload.get("compliance_threshold_percent") or 80.00,
        )

        record_audit_event(
            action="organizations.created",
            target_type="Organization",
            target_id=str(org.id),
            actor=request.user,
            organization=org,
            metadata={"code": org.code, "name": org.name},
        )
        return api_success(
            data=serialize_organization(org),
            message="Organization created.",
            status_code=status.HTTP_201_CREATED,
        )


class OrganizationDetailView(APIView):
    """GET, PATCH /api/v1/organizations/<uuid:org_id>/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Get organization details by ID",
        responses={200: OrganizationSerializer},
    )
    def get(self, request, org_id):
        org = get_object_or_404(Organization, id=org_id)
        if not request.user.is_platform_admin and not org.memberships.filter(
            user=request.user, is_active=True
        ).exists():
            record_audit_event(
                action="rbac.tenant_access_denied",
                target_type="Organization",
                target_id=str(org.id),
                actor=request.user,
            )
            raise PermissionDenied("You do not have access to this organization.")
        return api_success(data=serialize_organization(org))

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Update organization profile & governance settings",
        responses={200: OrganizationSerializer},
    )
    def patch(self, request, org_id):
        org = get_object_or_404(Organization, id=org_id)
        enforce_organization_permission(
            user=request.user,
            organization=org,
            permission_codename="organizations.manage",
            target_type="Organization",
            target_id=str(org.id),
        )
        payload = request.data or {}
        for field, key in (
            ("name", "name"),
            ("tier", "tier"),
            ("status", "status"),
            ("cac_number", "cacNumber"),
            ("contact_email", "contactEmail"),
            ("contact_phone", "contactPhone"),
            ("office_address", "officeAddress"),
            ("notes", "notes"),
            ("country", "country"),
        ):
            val = payload.get(key) if key in payload else payload.get(field)
            if val is not None:
                setattr(org, field, val)
        if "compliance_threshold_percent" in payload or "complianceThresholdPercent" in payload:
            org.compliance_threshold_percent = (
                payload.get("complianceThresholdPercent") or payload.get("compliance_threshold_percent")
            )
        org.save()

        record_audit_event(
            action="organizations.updated",
            target_type="Organization",
            target_id=str(org.id),
            actor=request.user,
            organization=org,
            metadata={"updated_fields": list(payload.keys())},
        )
        return api_success(data=serialize_organization(org), message="Organization updated.")


class MembershipListView(APIView):
    """GET, POST /api/v1/organizations/memberships/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="List organization memberships",
        description="Lists active and inactive organization membership records across tenants (Admin) or for the active organization.",
        responses={200: serializers.ListSerializer(child=serializers.DictField())},
    )
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            qs = OrganizationMembership.objects.select_related(
                "user", "organization", "role", "invited_by"
            ).all()
        else:
            org = resolve_request_organization(request)
            if not org:
                return api_success(data=[])
            qs = OrganizationMembership.objects.select_related(
                "user", "organization", "role", "invited_by"
            ).filter(organization=org)

        return api_success(data=[serialize_membership_record(m) for m in qs])

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Assign a user to an organization with an explicit RBAC role",
    )
    def post(self, request):
        payload = request.data or {}
        org_id = payload.get("organizationId") or payload.get("organization_id")
        if org_id:
            org = get_object_or_404(Organization, id=org_id)
        else:
            org = resolve_request_organization(request)
        if not org:
            raise ValidationError({"organizationId": ["Target organization is required."]})

        enforce_organization_permission(
            user=request.user,
            organization=org,
            permission_codename="organizations.invite_member",
            target_type="Organization",
            target_id=str(org.id),
        )

        user_id = payload.get("userId") or payload.get("user_id")
        email = (payload.get("email") or "").strip().lower()
        target_user = (
            User.objects.filter(id=user_id).first()
            if user_id
            else User.objects.filter(email=email).first()
        )
        if not target_user:
            raise ValidationError({"user": ["Target user account not found."]})

        role_slug = (payload.get("role") or SystemRoleSlug.PROJECT_ENGINEER).strip()
        if role_slug == SystemRoleSlug.PLATFORM_ADMIN and not request.user.is_platform_admin:
            record_audit_event(
                action="rbac.privilege_escalation_blocked",
                target_type="OrganizationMembership",
                target_id=str(target_user.id),
                actor=request.user,
                organization=org,
                metadata={"requested_role": role_slug},
            )
            raise PermissionDenied("Only Platform Administrators can assign the platform_admin role.")

        role = Role.objects.filter(slug=role_slug).first()
        if not role:
            raise ValidationError({"role": [f"Unknown system role '{role_slug}'."]})

        membership = add_user_to_organization(
            organization=org,
            user=target_user,
            role=role,
            invited_by=request.user,
        )

        record_audit_event(
            action="rbac.organization_role_assigned",
            target_type="OrganizationMembership",
            target_id=str(membership.id),
            actor=request.user,
            organization=org,
            metadata={
                "user_id": str(target_user.id),
                "user_email": target_user.email,
                "role": role.slug,
            },
        )
        return api_success(
            data=serialize_membership_record(membership),
            message="Organization membership and role assigned.",
            status_code=status.HTTP_201_CREATED,
        )


class MembershipRevokeView(APIView):
    """PATCH, DELETE /api/v1/organizations/memberships/<uuid:membership_id>/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Update an organization member's RBAC role or active status",
    )
    def patch(self, request, membership_id):
        membership = get_object_or_404(
            OrganizationMembership.objects.select_related("user", "organization", "role"),
            id=membership_id,
        )
        enforce_organization_permission(
            user=request.user,
            organization=membership.organization,
            permission_codename="organizations.invite_member",
            target_type="OrganizationMembership",
            target_id=str(membership.id),
        )

        payload = request.data or {}
        previous_role = membership.role.slug if membership.role else ""
        new_role_slug = (payload.get("role") or "").strip()

        if new_role_slug:
            if new_role_slug == SystemRoleSlug.PLATFORM_ADMIN and not request.user.is_platform_admin:
                record_audit_event(
                    action="rbac.privilege_escalation_blocked",
                    target_type="OrganizationMembership",
                    target_id=str(membership.id),
                    actor=request.user,
                    organization=membership.organization,
                    metadata={"requested_role": new_role_slug, "previous_role": previous_role},
                )
                raise PermissionDenied("Only Platform Administrators can assign the platform_admin role.")

            new_role = Role.objects.filter(slug=new_role_slug).first()
            if not new_role:
                raise ValidationError({"role": [f"Unknown system role '{new_role_slug}'."]})
            membership.role = new_role

        if "is_active" in payload or "isActive" in payload:
            membership.is_active = bool(
                payload.get("isActive") if "isActive" in payload else payload.get("is_active")
            )

        membership.save()

        record_audit_event(
            action="rbac.organization_role_changed",
            target_type="OrganizationMembership",
            target_id=str(membership.id),
            actor=request.user,
            organization=membership.organization,
            metadata={
                "user_email": membership.user.email,
                "previous_role": previous_role,
                "new_role": membership.role.slug,
                "is_active": membership.is_active,
            },
        )
        return api_success(
            data=serialize_membership_record(membership),
            message="Organization membership updated.",
        )

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Revoke an organization membership",
        responses={200: OpenApiTypes.OBJECT},
    )
    def delete(self, request, membership_id):
        membership = get_object_or_404(
            OrganizationMembership.objects.select_related("user", "organization", "role"),
            id=membership_id,
        )
        enforce_organization_permission(
            user=request.user,
            organization=membership.organization,
            permission_codename="organizations.invite_member",
            target_type="OrganizationMembership",
            target_id=str(membership.id),
        )

        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])

        record_audit_event(
            action="MEMBERSHIP_REVOKED",
            target_type="MembershipRecord",
            target_id=str(membership.id),
            actor=request.user,
            organization=membership.organization,
            metadata={"user_email": membership.user.email, "role": membership.role.slug},
        )
        return api_success(data={"revoked": True, "id": str(membership.id)})


class InvitationListCreateView(APIView):
    """GET, POST /api/v1/organizations/invitations/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="List organization invitations",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            qs = OrganizationInvitation.objects.select_related("organization", "role", "invited_by").all()
        else:
            org = resolve_request_organization(request)
            if not org:
                return api_success(data=[])
            qs = OrganizationInvitation.objects.select_related(
                "organization", "role", "invited_by"
            ).filter(organization=org)

        return api_success(data=[serialize_invitation(inv) for inv in qs])

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Issue a new organization invitation",
        request=CreateInvitationRequestSerializer,
        responses={201: OpenApiTypes.OBJECT},
        examples=[
            OpenApiExample(
                "Invite Field Inspector Example",
                value={
                    "email": "inspector.new@civora.ng",
                    "role": "field_personnel",
                    "assignedProjects": ["LCR-S1-2026"],
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        payload = request.data or {}
        email = (payload.get("email") or "").strip().lower()
        role_slug = payload.get("role") or SystemRoleSlug.PROJECT_ENGINEER
        org_id = payload.get("organizationId") or payload.get("organization_id")

        if not email:
            raise ValidationError({"email": ["Recipient email is required."]})

        if org_id:
            org = Organization.objects.filter(id=org_id).first()
        else:
            org = resolve_request_organization(request) or (
                Organization.objects.filter(is_active=True).first()
                if request.user.is_platform_admin
                else None
            )

        if not org:
            raise ValidationError({"organizationId": ["Valid organization is required."]})

        enforce_organization_permission(
            user=request.user,
            organization=org,
            permission_codename="organizations.invite_member",
            target_type="OrganizationInvitation",
            target_id=str(org.id),
        )

        if role_slug == SystemRoleSlug.PLATFORM_ADMIN and not request.user.is_platform_admin:
            record_audit_event(
                action="rbac.privilege_escalation_blocked",
                target_type="OrganizationInvitation",
                target_id=email,
                actor=request.user,
                organization=org,
                metadata={"requested_role": role_slug},
            )
            raise PermissionDenied("Only Platform Administrators can issue platform_admin invitations.")

        role = Role.objects.filter(slug=role_slug).first() or Role.objects.filter(
            slug=SystemRoleSlug.PROJECT_ENGINEER
        ).first()

        inv_code = f"CIV-INV-{random.randint(1000, 9999)}"
        while OrganizationInvitation.objects.filter(invitation_code=inv_code).exists():
            inv_code = f"CIV-INV-{random.randint(1000, 9999)}"

        inv = OrganizationInvitation.objects.create(
            organization=org,
            email=email,
            role=role,
            invitation_code=inv_code,
            status="pending",
            assigned_project_codes=payload.get("assignedProjects") or [],
            invited_by=request.user,
            expires_at=timezone.now() + timedelta(days=7),
        )

        record_audit_event(
            action="INVITATION_ISSUED",
            target_type="UserInvitation",
            target_id=str(inv.id),
            actor=request.user,
            organization=org,
            metadata={"email": email, "role": role.slug, "invitation_code": inv_code},
        )
        return api_success(
            data=serialize_invitation(inv),
            message="Invitation issued.",
            status_code=status.HTTP_201_CREATED,
        )


class InvitationRevokeView(APIView):
    """POST /api/v1/organizations/invitations/<uuid:invitation_id>/revoke/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Organizations & Invitations"],
        summary="Revoke a pending organization invitation",
        request=None,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request, invitation_id):
        inv = get_object_or_404(
            OrganizationInvitation.objects.select_related("organization"),
            id=invitation_id,
        )
        enforce_organization_permission(
            user=request.user,
            organization=inv.organization,
            permission_codename="organizations.invite_member",
            target_type="UserInvitation",
            target_id=str(inv.id),
        )

        inv.status = "revoked"
        inv.save(update_fields=["status", "updated_at"])

        record_audit_event(
            action="INVITATION_REVOKED",
            target_type="UserInvitation",
            target_id=str(inv.id),
            actor=request.user,
            organization=inv.organization,
            metadata={"email": inv.email, "invitation_code": inv.invitation_code},
        )
        return api_success(data=serialize_invitation(inv), message="Invitation revoked.")
