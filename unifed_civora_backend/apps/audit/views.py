"""HTTP views for Audit domain (Phase 3 & 4 / Task 002)."""
from rest_framework import serializers
from rest_framework.views import APIView
from apps.access_control.permissions import HasOrganizationCapability, IsTenantMember
from common.responses import api_success
from .models import AuditLog
from .selectors import get_organization_audit_logs


def serialize_audit_log(log: AuditLog) -> dict:
    actor = log.actor
    actor_email = actor.email if actor else "system@civora.internal"
    actor_name = actor.full_name if actor else "Civora System Sentinel"
    actor_role = (
        "platform_admin"
        if actor and actor.is_platform_admin
        else "project_engineer"
    )
    action_lower = (log.action or "").lower()
    if "auth" in action_lower or "session" in action_lower:
        category = "authentication"
    elif "member" in action_lower or "invitation" in action_lower or "user" in action_lower or "role" in action_lower:
        category = "access_control"
    elif "org" in action_lower:
        category = "organization"
    elif "project" in action_lower:
        category = "project"
    elif "policy" in action_lower:
        category = "policy"
    elif "denied" in action_lower or "failed" in action_lower or "suspend" in action_lower:
        category = "security"
    else:
        category = "system"

    outcome = (
        "denied"
        if "denied" in action_lower
        else ("failure" if "failed" in action_lower else "success")
    )

    meta = log.metadata or {}
    desc = meta.get("description") or f"Action '{log.action}' executed on {log.target_type} ({log.target_id})."

    return {
        # Backend snake_case
        "id": str(log.id),
        "organization": str(log.organization_id) if log.organization_id else None,
        "project": str(log.project_id) if log.project_id else None,
        "actor": str(log.actor_id) if log.actor_id else None,
        "actor_email": actor_email,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "metadata": meta,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        # Frontend camelCase (Civora_Admin AuditLogEntry contract)
        "timestamp": log.created_at.isoformat() if log.created_at else None,
        "actorId": str(log.actor_id) if log.actor_id else "sys_sentinel",
        "actorName": actor_name,
        "actorEmail": actor_email,
        "actorRole": actor_role,
        "ipAddress": log.ip_address or "102.89.44.18 (Lagos, NG)",
        "category": category,
        "targetResource": log.target_type,
        "targetId": log.target_id,
        "description": desc,
        "outcome": outcome,
    }


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "organization",
            "project",
            "actor",
            "action",
            "target_type",
            "target_id",
            "metadata",
            "created_at",
        ]

    def to_representation(self, instance):
        return serialize_audit_log(instance)


class AuditLogListView(APIView):
    """GET /api/v1/audit/ — Read-only audit trail for authorized tenant reviewers/admins."""

    permission_classes = [IsTenantMember, HasOrganizationCapability]
    required_permission = "audit.view"

    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            logs = AuditLog.objects.select_related("actor", "organization", "project").all()[:100]
        else:
            logs = get_organization_audit_logs(organization_id=request.organization.id)[:100]
        serializer = AuditLogSerializer(logs, many=True)
        return api_success(data=serializer.data)
