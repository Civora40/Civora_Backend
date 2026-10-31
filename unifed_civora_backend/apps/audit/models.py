"""
Immutable AuditLog model (Phase 2, 3, and 10).
Every sensitive change, permission update, and workflow transition is recorded here.
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from common.models import TimestampedModel


class AuditLog(TimestampedModel):
    """
    Append-only audit trail entry.
    Cannot be updated or deleted once persisted.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        db_index=True,
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_actions",
    )
    action = models.CharField(max_length=120, db_index=True)
    target_type = models.CharField(max_length=80, db_index=True)
    target_id = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "civora_audit_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "created_at"], name="idx_audit_org_created"),
            models.Index(fields=["project", "created_at"], name="idx_audit_proj_created"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied("AuditLog records are immutable and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("AuditLog records are immutable and cannot be deleted.")

    def __str__(self) -> str:
        actor_email = self.actor.email if self.actor else "system"
        return f"[{self.action}] by {actor_email} on {self.target_type}:{self.target_id}"

