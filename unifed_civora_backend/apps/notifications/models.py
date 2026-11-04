"""
Notification & escalation delivery models (Phase 2 — Database Architecture and Data Modelling):
Notification, NotificationPreference, and NotificationDeliveryLog.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from common.models import TenantScopedModel, TimestampedModel


class NotificationChannel(models.TextChoices):
    IN_APP = "in_app", "In-App Notification Center"
    EMAIL = "email", "Email Dispatch"
    WHATSAPP = "whatsapp", "WhatsApp Alert"
    TELEGRAM = "telegram", "Telegram Alert"
    PUSH = "push", "Mobile Push Notification"


class NotificationSeverity(models.TextChoices):
    INFO = "info", "Informational"
    WARNING = "warning", "Warning / Action Required"
    CRITICAL = "critical", "Critical Escalation"
    SUCCESS = "success", "Completed / Verified"


class DeliveryStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent to Provider"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped by Preference"


class Notification(TenantScopedModel):
    """
    In-app and multi-channel notification event targeted at a User within an Organization (Phase 2).
    """

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
        db_index=True,
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_notifications",
    )
    event_type = models.CharField(
        max_length=80,
        db_index=True,
        help_text="e.g., submission.received, task.overdue, report.review_requested, report.published",
    )
    severity = models.CharField(
        max_length=20,
        choices=NotificationSeverity.choices,
        default=NotificationSeverity.INFO,
        db_index=True,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    action_url = models.CharField(max_length=500, blank=True, default="")
    target_type = models.CharField(max_length=80, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["recipient", "is_read", "created_at"],
                name="idx_notif_recipient_unread",
            ),
            models.Index(
                fields=["organization", "project", "event_type"],
                name="idx_notif_org_proj_event",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.project_id and self.organization_id:
            if self.project.organization_id != self.organization_id:
                raise ValidationError(
                    "Cross-tenant integrity violation: Notification project does not belong to the specified Organization."
                )

    def __str__(self) -> str:
        return f"[{self.event_type}] -> {self.recipient.email}: {self.title}"


class NotificationPreference(TimestampedModel):
    """
    Per-user notification channel routing and quiet-hours configuration (Phase 2).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_notification_preferences",
    )
    in_app_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    whatsapp_enabled = models.BooleanField(default=False)
    telegram_enabled = models.BooleanField(default=False)
    daily_digest_enabled = models.BooleanField(default=True)
    escalation_alerts_enabled = models.BooleanField(default=True)
    muted_event_types = models.JSONField(default=list, blank=True)
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_notification_preferences"

    def __str__(self) -> str:
        return f"NotificationPreference({self.user.email})"


class NotificationDeliveryLog(TenantScopedModel):
    """
    Auditable delivery attempt log for each outbound channel dispatch of a Notification (Phase 2).
    """

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="delivery_logs",
        db_index=True,
    )
    channel = models.CharField(
        max_length=24,
        choices=NotificationChannel.choices,
        db_index=True,
    )
    recipient_address = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=24,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.QUEUED,
        db_index=True,
    )
    provider_message_id = models.CharField(max_length=120, blank=True, default="")
    attempt_count = models.PositiveSmallIntegerField(default=1)
    error_message = models.TextField(blank=True, default="")
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_notification_delivery_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["notification", "channel", "status"],
                name="idx_notif_delivery_status",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.notification_id and self.organization_id:
            if self.notification.organization_id != self.organization_id:
                raise ValidationError(
                    "NotificationDeliveryLog organization must match its parent Notification organization."
                )

    def __str__(self) -> str:
        return f"Delivery [{self.channel}] for {self.notification_id} -> {self.status}"
