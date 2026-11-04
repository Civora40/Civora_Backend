"""
Governance, review, and sign-off models (Phase 2 — Database Architecture and Data Modelling):
ReportReview, ReviewComment, and immutable Approval records.
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.utils import timezone
from common.models import ProjectScopedModel


class ReviewDecision(models.TextChoices):
    PENDING = "pending", "Pending Review"
    APPROVED = "approved", "Approved"
    CHANGES_REQUESTED = "changes_requested", "Changes Requested"
    REJECTED = "rejected", "Rejected"


class ReportReview(ProjectScopedModel):
    """
    Supervising engineer or technical reviewer evaluation of a Report / ReportVersion (Phase 2).
    """

    report = models.ForeignKey(
        "reports.Report",
        on_delete=models.CASCADE,
        related_name="reviews",
        db_index=True,
    )
    report_version = models.ForeignKey(
        "reports.ReportVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_report_reviews",
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_report_reviews",
    )
    decision = models.CharField(
        max_length=32,
        choices=ReviewDecision.choices,
        default=ReviewDecision.PENDING,
        db_index=True,
    )
    summary_notes = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_report_reviews"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "project", "decision"],
                name="idx_report_review_decision",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.report_id and self.project_id:
            if self.report.project_id != self.project_id:
                raise ValidationError("ReportReview project must match its Report project.")
        if self.report_version_id and self.report_id:
            if self.report_version.report_id != self.report_id:
                raise ValidationError("ReportVersion does not belong to the specified Report.")

    def __str__(self) -> str:
        reviewer_email = self.reviewer.email if self.reviewer else "unassigned"
        return f"Review of {self.report.reference_code} by {reviewer_email} ({self.decision})"


class ReviewComment(ProjectScopedModel):
    """
    Inline or section-level technical review comment on a ReportReview (Phase 2).
    """

    review = models.ForeignKey(
        ReportReview,
        on_delete=models.CASCADE,
        related_name="comments",
        db_index=True,
    )
    section = models.ForeignKey(
        "reports.ReportSection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authored_review_comments",
    )
    body = models.TextField()
    is_resolved = models.BooleanField(default=False, db_index=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_review_comments",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_review_comments"
        ordering = ["created_at"]

    def clean(self) -> None:
        super().clean()
        if self.review_id and self.project_id:
            if self.review.project_id != self.project_id:
                raise ValidationError("ReviewComment project must match its ReportReview project.")

    def __str__(self) -> str:
        return f"Comment on {self.review.report.reference_code} (resolved={self.is_resolved})"


class Approval(ProjectScopedModel):
    """
    Formal, immutable regulatory/engineering sign-off record for a ReportVersion (Phase 2).
    Protected against deletion to preserve legal and compliance audit trails.
    """

    report = models.ForeignKey(
        "reports.Report",
        on_delete=models.PROTECT,
        related_name="approvals",
        db_index=True,
    )
    report_version = models.ForeignKey(
        "reports.ReportVersion",
        on_delete=models.PROTECT,
        related_name="approvals",
    )
    review = models.OneToOneField(
        ReportReview,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="formal_approval",
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="granted_approvals",
    )
    approver_role_slug = models.CharField(max_length=64)
    approver_coren_number = models.CharField(max_length=64, blank=True, default="")
    signature_hash = models.CharField(
        max_length=128,
        help_text="Cryptographic SHA-256 attestation digest binding approver, report version, and timestamp.",
    )
    certification_statement = models.TextField(blank=True, default="")
    approved_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "civora_approvals"
        ordering = ["-approved_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["report_version", "approver"],
                name="uq_approval_report_version_approver",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if self.report_id and self.project_id:
            if self.report.project_id != self.project_id:
                raise ValidationError("Approval project must match its Report project.")
        if self.report_version_id and self.report_id:
            if self.report_version.report_id != self.report_id:
                raise ValidationError("Approval ReportVersion must belong to the specified Report.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied("Approval sign-off records are immutable and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Approval sign-off records are immutable and cannot be deleted.")

    def __str__(self) -> str:
        return f"Approval {self.report.reference_code} v{self.report_version.version_number} by {self.approver.email}"
