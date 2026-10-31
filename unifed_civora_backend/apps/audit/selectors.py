"""Read selectors for AuditLog domain."""
from django.db.models import QuerySet
from .models import AuditLog


def get_organization_audit_logs(*, organization_id) -> QuerySet[AuditLog]:
    if not organization_id:
        return AuditLog.objects.none()
    return AuditLog.objects.select_related("actor", "project").filter(
        organization_id=organization_id
    )

