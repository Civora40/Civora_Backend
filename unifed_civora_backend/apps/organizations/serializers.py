"""Serializers for Organizations domain (Phase 4 / Task 002)."""
from rest_framework import serializers
from .models import Organization, OrganizationInvitation, OrganizationMembership


def serialize_organization(org: Organization) -> dict:
    active_projects_count = getattr(org, "active_projects_count", None)
    if active_projects_count is None:
        active_projects_count = org.projects_project_records.exclude(status="archived").count()
    total_members_count = getattr(org, "total_members_count", None)
    if total_members_count is None:
        total_members_count = org.memberships.filter(is_active=True).count()

    return {
        # Backend snake_case
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "code": org.code,
        "tier": org.tier,
        "status": org.status if org.is_active else "inactive",
        "cac_number": org.cac_number,
        "contact_email": org.contact_email,
        "contact_phone": org.contact_phone,
        "office_address": org.office_address,
        "country": org.country,
        "compliance_threshold_percent": float(org.compliance_threshold_percent),
        "is_active": org.is_active,
        "notes": org.notes,
        "created_at": org.created_at.isoformat() if org.created_at else None,
        "updated_at": org.updated_at.isoformat() if org.updated_at else None,
        # Frontend camelCase (Civora_Admin Organization contract)
        "cacNumber": org.cac_number,
        "contactEmail": org.contact_email or f"operations@{org.slug}.com",
        "contactPhone": org.contact_phone or "+234 1 270 3000",
        "officeAddress": org.office_address or f"{org.name} HQ, {org.country}",
        "activeProjectsCount": active_projects_count,
        "totalMembersCount": total_members_count,
        "createdAt": org.created_at.isoformat() if org.created_at else None,
        "updatedAt": org.updated_at.isoformat() if org.updated_at else None,
    }


def serialize_membership_record(membership: OrganizationMembership) -> dict:
    proj_memberships = list(
        membership.user.project_memberships.select_related("project")
        .filter(organization_id=membership.organization_id, is_active=True)
    )
    if membership.user.is_platform_admin:
        assigned_ids = ["*"]
        assigned_names = ["All Active Platform Projects"]
        access_level = "full_admin"
    else:
        assigned_ids = [str(pm.project_id) for pm in proj_memberships]
        assigned_names = [pm.project.name for pm in proj_memberships if pm.project]
        role_slug = membership.role.slug if membership.role else "project_engineer"
        if role_slug == "supervising_reviewer":
            access_level = "technical_reviewer"
        elif role_slug == "read_only_stakeholder":
            access_level = "auditor"
        else:
            access_level = "project_lead"

    return {
        "id": str(membership.id),
        "organization": str(membership.organization_id),
        "user": str(membership.user_id),
        "userId": str(membership.user_id),
        "userName": membership.user.full_name,
        "userEmail": membership.user.email,
        "userRole": (
            "platform_admin"
            if membership.user.is_platform_admin
            else (
                "read_only_stakeholder"
                if membership.role and membership.role.slug == "read_only_stakeholder"
                else "project_engineer"
            )
        ),
        "role": membership.role.slug if membership.role else "project_engineer",
        "role_slug": membership.role.slug if membership.role else "project_engineer",
        "organizationId": str(membership.organization_id),
        "organizationName": membership.organization.name if membership.organization else "",
        "assignedProjectIds": assigned_ids,
        "assignedProjectNames": assigned_names,
        "accessLevel": access_level,
        "grantedAt": membership.created_at.isoformat() if membership.created_at else None,
        "grantedBy": membership.invited_by.email if membership.invited_by else "System Bootstrap",
        "status": "active" if membership.is_active else "revoked",
        "is_active": membership.is_active,
    }


def serialize_invitation(inv: OrganizationInvitation) -> dict:
    return {
        "id": str(inv.id),
        "email": inv.email,
        "role": (
            "platform_admin"
            if inv.role.slug == "platform_admin"
            else (
                "read_only_stakeholder"
                if inv.role.slug == "read_only_stakeholder"
                else "project_engineer"
            )
        ),
        "organizationId": str(inv.organization_id),
        "organizationName": inv.organization.name if inv.organization else "",
        "invitedBy": inv.invited_by.email if inv.invited_by else "admin@civora.internal",
        "invitedAt": inv.created_at.isoformat() if inv.created_at else None,
        "expiresAt": inv.expires_at.isoformat() if inv.expires_at else None,
        "status": inv.status,
        "invitationCode": inv.invitation_code,
        "invitation_code": inv.invitation_code,
        "role_slug": inv.role.slug if inv.role else "project_engineer",
        "assignedProjects": inv.assigned_project_codes or [],
        "assigned_projects": inv.assigned_project_codes or [],
    }


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "code",
            "tier",
            "status",
            "cac_number",
            "contact_email",
            "contact_phone",
            "office_address",
            "country",
            "compliance_threshold_percent",
            "is_active",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def to_representation(self, instance):
        return serialize_organization(instance)


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationMembership
        fields = [
            "id",
            "organization",
            "user",
            "role",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def to_representation(self, instance):
        return serialize_membership_record(instance)
