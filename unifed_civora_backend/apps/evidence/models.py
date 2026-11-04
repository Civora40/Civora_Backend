"""
Evidence & media attachment models (Phase 2 — Database Architecture and Data Modelling):
SubmissionAttachment scoped to Organization and Project with SHA-256 integrity metadata.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from common.models import ProjectScopedModel


class AttachmentMediaType(models.TextChoices):
    PHOTO = "photo", "Site Photograph"
    VIDEO = "video", "Video Walkthrough"
    AUDIO_VOICE_NOTE = "audio_voice_note", "Voice Note / Audio"
    DOCUMENT_PDF = "document_pdf", "PDF Document / Certificate"
    SPREADSHEET = "spreadsheet", "BOQ / Spreadsheet"
    DRAWING_CAD = "drawing_cad", "Engineering Drawing / CAD"
    OTHER = "other", "Other Attachment"


class SubmissionAttachment(ProjectScopedModel):
    """
    Verified media or document evidence attached to a field Submission, Task, or Project (Phase 2).
    Stores object storage keys, cryptographic SHA-256 checksums, and EXIF/GPS telemetry.
    """

    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
        db_index=True,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_attachments",
    )
    file_name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=512, unique=True, db_index=True)
    file_url = models.URLField(max_length=1024, blank=True, default="")
    mime_type = models.CharField(max_length=120, default="application/octet-stream")
    media_type = models.CharField(
        max_length=32,
        choices=AttachmentMediaType.choices,
        default=AttachmentMediaType.PHOTO,
        db_index=True,
    )
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    sha256_checksum = models.CharField(max_length=64, blank=True, default="", db_index=True)
    caption = models.CharField(max_length=500, blank=True, default="")
    captured_at = models.DateTimeField(null=True, blank=True)
    gps_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    exif_metadata = models.JSONField(default=dict, blank=True)
    is_verified = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "civora_submission_attachments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "project", "media_type"],
                name="idx_attachment_proj_media",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.submission_id and self.project_id:
            if self.submission.project_id != self.project_id:
                raise ValidationError(
                    "SubmissionAttachment project must match its parent Submission project."
                )

    def __str__(self) -> str:
        return f"{self.file_name} ({self.media_type})"
