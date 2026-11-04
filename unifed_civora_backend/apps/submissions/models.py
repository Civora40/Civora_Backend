"""
Field operations models (Phase 2 — Database Architecture and Data Modelling):
Submission and SubmissionStatusHistory scoped to Organization and Project.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from common.models import ProjectScopedModel


class SubmissionChannel(models.TextChoices):
    MOBILE_APP = "mobile_app", "Civora Mobile App"
    WEB_PORTAL = "web_portal", "Civora Web Portal"
    WHATSAPP = "whatsapp", "WhatsApp Business Channel"
    TELEGRAM = "telegram", "Telegram Field Bot"
    API_INGEST = "api_ingest", "Direct API Ingestion"


class SubmissionType(models.TextChoices):
    DAILY_PROGRESS = "daily_progress", "Daily Site Progress"
    INCIDENT_REPORT = "incident_report", "Incident / Safety Report"
    QUALITY_INSPECTION = "quality_inspection", "QA/QC Inspection & Testing"
    MATERIAL_DELIVERY = "material_delivery", "Material Delivery & Waybill"
    HSE_OBSERVATION = "hse_observation", "HSE Observation"
    DELAY_NOTICE = "delay_notice", "Site Delay / Blocker Notice"
    GENERAL_UPDATE = "general_update", "General Field Update"


class SubmissionStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    VALIDATING = "validating", "Validating"
    PENDING_REVIEW = "pending_review", "Pending Engineer Review"
    APPROVED = "approved", "Approved & Verified"
    NEEDS_CLARIFICATION = "needs_clarification", "Needs Clarification"
    REJECTED = "rejected", "Rejected"
    ARCHIVED = "archived", "Archived"


class Submission(ProjectScopedModel):
    """
    Structured field submission captured from the mobile app, web portal,
    WhatsApp, or Telegram and linked to a specific Project (Phase 2).
    """

    reference_code = models.CharField(max_length=60, db_index=True)
    department = models.ForeignKey(
        "projects.ProjectDepartment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    activity = models.ForeignKey(
        "projects.ProjectActivity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="field_submissions",
        db_index=True,
    )
    submitter_name = models.CharField(max_length=150, blank=True, default="")
    submitter_identifier = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Sender phone number, Telegram handle, or email when ingested from external channels.",
    )
    channel = models.CharField(
        max_length=32,
        choices=SubmissionChannel.choices,
        default=SubmissionChannel.WEB_PORTAL,
        db_index=True,
    )
    submission_type = models.CharField(
        max_length=40,
        choices=SubmissionType.choices,
        default=SubmissionType.DAILY_PROGRESS,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING_REVIEW,
        db_index=True,
    )
    title = models.CharField(max_length=255)
    raw_text = models.TextField(blank=True, default="")
    structured_payload = models.JSONField(default=dict, blank=True)
    gps_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_label = models.CharField(max_length=255, blank=True, default="")
    weather_condition = models.CharField(max_length=120, blank=True, default="")
    completeness_score = models.PositiveSmallIntegerField(default=100)
    is_late = models.BooleanField(default=False, db_index=True)
    external_message_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_submissions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "civora_submissions"
        ordering = ["-submitted_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "reference_code"],
                name="uq_project_submission_reference",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "project", "status"],
                name="idx_submission_proj_status",
            ),
            models.Index(
                fields=["project", "channel", "submitted_at"],
                name="idx_submission_proj_channel",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.department_id and self.project_id:
            if self.department.project_id != self.project_id:
                raise ValidationError(
                    "Submission department does not belong to the specified Project."
                )
        if self.activity_id and self.project_id:
            if self.activity.project_id != self.project_id:
                raise ValidationError(
                    "Submission activity does not belong to the specified Project."
                )

    def __str__(self) -> str:
        return f"{self.reference_code}: {self.title} ({self.status})"


class SubmissionStatusHistory(ProjectScopedModel):
    """
    Immutable status transition log for a Submission (Phase 2).
    Tracks who transitioned a submission, when, and with what justification.
    """

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="status_history",
        db_index=True,
    )
    from_status = models.CharField(max_length=32, blank=True, default="")
    to_status = models.CharField(
        max_length=32,
        choices=SubmissionStatus.choices,
        db_index=True,
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submission_status_changes",
    )
    reason = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "civora_submission_status_history"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["submission", "created_at"],
                name="idx_sub_history_created",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.submission_id and self.project_id:
            if self.submission.project_id != self.project_id:
                raise ValidationError(
                    "SubmissionStatusHistory project must match its parent Submission project."
                )

    def __str__(self) -> str:
        return f"{self.submission.reference_code}: {self.from_status or 'initial'} -> {self.to_status}"
