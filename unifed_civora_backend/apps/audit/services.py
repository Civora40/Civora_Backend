"""Append-only audit logging service."""
from typing import Any, Dict, Optional
from .models import AuditLog


def record_audit_event(
    *,
    action: str,
    target_type: str,
    target_id: str = "",
    actor=None,
    organization=None,
    project=None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    return AuditLog.objects.create(
        organization=organization,
        project=project,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata or {},
        ip_address=ip_address,
    )

