"""
External channel integrations domain models (Phase 6).
Provides secure, provider-independent models for webhook events, channel bindings,
verified contact resolution, and ingestion telemetry.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from common.models import ProjectScopedModel, TimestampedModel


class ExternalChannel(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp Business Platform"
    TELEGRAM = "telegram", "Telegram Field Bot"
    WEB_PORTAL = "web_portal", "Civora Web Portal"
    SMS = "sms", "SMS Gateway"
    EMAIL = "email", "Inbound Email Dispatch"


class EventStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSED = "processed", "Processed & Ingested"
    DUPLICATE_BLOCKED = "duplicate_blocked", "Duplicate Blocked"
    NEEDS_VERIFICATION = "needs_verification", "Needs Identity Verification"
    UNRESOLVED_PROJECT = "unresolved_project", "Unresolved Project Binding"
    FAILED = "failed", "Ingestion Failed"


class ContactType(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp Phone Number"
    TELEGRAM = "telegram", "Telegram Handle / User ID"
    PHONE = "phone", "Phone (SMS / Voice)"
    EMAIL = "email", "Email Address"


class IngestionRunStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    PARTIAL_FAILURE = "partial_failure", "Partial Failure"
    FAILED = "failed", "Failed"
    IN_PROGRESS = "in_progress", "In Progress"


class InboundWebhookEvent(TimestampedModel):
    """
    Immutable raw audit trail for all inbound webhooks from external channels.
    Enforces strict message-level idempotency to prevent duplicate submissions.
    """

    channel = models.CharField(
        max_length=32,
        choices=ExternalChannel.choices,
        default=ExternalChannel.WHATSAPP,
        db_index=True,
    )
    provider_event_id = models.CharField(max_length=255, db_index=True)
    provider_message_id = models.CharField(max_length=255, db_index=True)
    sender_raw = models.CharField(
        max_length=150,
        blank=True,
        default="",
        db_index=True,
        help_text="Raw sender phone number or Telegram handle.",
    )
    external_chat_id = models.CharField(
        max_length=150,
        blank=True,
        default="",
        db_index=True,
        help_text="Group chat_id or channel identifier.",
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Exact, unmutated JSON payload received from external provider.",
    )
    status = models.CharField(
        max_length=32,
        choices=EventStatus.choices,
        default=EventStatus.RECEIVED,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_webhook_events",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_webhook_events",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_events",
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_inbound_webhook_events"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "provider_message_id"],
                name="uq_channel_provider_msg_id",
            )
        ]
        indexes = [
            models.Index(
                fields=["channel", "status", "created_at"],
                name="idx_inbound_evt_status",
            ),
            models.Index(
                fields=["provider_message_id"],
                name="idx_inbound_msg_id",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.channel.upper()}] {self.provider_message_id} ({self.status})"


class ChannelBinding(ProjectScopedModel):
    """
    Binds an external communication channel (Telegram group, WhatsApp business number)
    to a specific Project and optional default Department.
    """

    channel = models.CharField(
        max_length=32,
        choices=ExternalChannel.choices,
        default=ExternalChannel.WHATSAPP,
        db_index=True,
    )
    external_identifier = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Telegram chat_id, WhatsApp group ID, or dedicated phone number.",
    )
    label = models.CharField(
        max_length=255,
        help_text="Descriptive name (e.g. 'Lagos Rail HSE Telegram Group').",
    )
    department = models.ForeignKey(
        "projects.ProjectDepartment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="channel_bindings",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Channel-specific configuration options.",
    )

    class Meta:
        db_table = "civora_channel_bindings"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_identifier"],
                name="uq_channel_binding_ext_id",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if self.department_id and self.project_id:
            if self.department.project_id != self.project_id:
                raise ValidationError("Department does not belong to the bound Project.")

    def __str__(self) -> str:
        return f"{self.get_channel_display()}: {self.label} -> {self.project.code}"


class VerifiedContactMethod(TimestampedModel):
    """
    Associates a verified external communication identifier (E.164 phone number,
    Telegram @username or user ID) with a Civora User for automatic field attribution.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="verified_contacts",
    )
    contact_type = models.CharField(
        max_length=32,
        choices=ContactType.choices,
        default=ContactType.WHATSAPP,
        db_index=True,
    )
    identifier = models.CharField(
        max_length=150,
        db_index=True,
        help_text="Normalized E.164 phone (+234...) or Telegram @username / numeric user ID.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    is_primary = models.BooleanField(default=False)
    verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "civora_verified_contacts"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact_type", "identifier"],
                name="uq_contact_type_identifier",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.email} [{self.contact_type}]: {self.identifier}"


class IngestionRun(TimestampedModel):
    """
    Batch telemetry and Dead-Letter Queue (DLQ) tracker for messaging integrations.
    Surfaces ingestion runs, duplicate metrics, and retry controls to Platform Admins.
    """

    batch_reference = models.CharField(max_length=100, unique=True, db_index=True)
    channel = models.CharField(
        max_length=32,
        choices=ExternalChannel.choices,
        default=ExternalChannel.WHATSAPP,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=IngestionRunStatus.choices,
        default=IngestionRunStatus.SUCCESS,
        db_index=True,
    )
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    total_messages_ingested = models.PositiveIntegerField(default=0)
    duplicates_blocked = models.PositiveIntegerField(default=0)
    unresolved_senders = models.PositiveIntegerField(default=0)
    media_processing_failures = models.PositiveIntegerField(default=0)
    retry_attempts = models.PositiveIntegerField(default=0)
    error_log_snippet = models.TextField(blank=True, default="")

    class Meta:
        db_table = "civora_ingestion_runs"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.batch_reference} [{self.channel}]: {self.status}"
