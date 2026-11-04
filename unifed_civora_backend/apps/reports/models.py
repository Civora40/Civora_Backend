"""
Reporting engine models (Phase 2 — Database Architecture and Data Modelling):
ReportTemplate, Report, ReportVersion, ReportSection, and ReportEvidenceLink.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from common.models import ProjectScopedModel, TenantScopedModel


class ReportType(models.TextChoices):
    DAILY_FIELD = "daily_field", "Daily Field Report"
    WEEKLY_PROGRESS = "weekly_progress", "Weekly Engineering Progress Report"
    MONTHLY_EXECUTIVE = "monthly_executive", "Monthly Executive & Stakeholder Dossier"
    INCIDENT_INVESTIGATION = "incident_investigation", "Incident & Root Cause Investigation"
    QA_QC_COMPLIANCE = "qa_qc_compliance", "QA/QC Structural & Materials Compliance"
    STAKEHOLDER_BRIEF = "stakeholder_brief", "Stakeholder Oversight Brief"


class ReportStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_REVIEW = "in_review", "In Supervising Review"
    CHANGES_REQUESTED = "changes_requested", "Changes Requested"
    APPROVED = "approved", "Approved"
    PUBLISHED = "published", "Published to Stakeholders"
    ARCHIVED = "archived", "Archived"


class ReportTemplate(TenantScopedModel):
    """
    Standardized report structure and schema owned by an Organization (Phase 2).
    """

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=60, db_index=True)
    report_type = models.CharField(
        max_length=40,
        choices=ReportType.choices,
        default=ReportType.WEEKLY_PROGRESS,
        db_index=True,
    )
    description = models.TextField(blank=True, default="")
    schema_definition = models.JSONField(default=dict, blank=True)
    default_sections = models.JSONField(default=list, blank=True)
    is_system_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_report_templates",
    )

    class Meta:
        db_table = "civora_report_templates"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="uq_org_report_template_code",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"


class Report(ProjectScopedModel):
    """
    Formal engineering or stakeholder report generated for a Project (Phase 2).
    Supports versioning, section-by-section authoring, evidence citations, and sign-off.
    """

    template = models.ForeignKey(
        ReportTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    reference_code = models.CharField(max_length=60, db_index=True)
    title = models.CharField(max_length=255)
    report_type = models.CharField(
        max_length=40,
        choices=ReportType.choices,
        default=ReportType.WEEKLY_PROGRESS,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=ReportStatus.choices,
        default=ReportStatus.DRAFT,
        db_index=True,
    )
    reporting_period_start = models.DateField(null=True, blank=True)
    reporting_period_end = models.DateField(null=True, blank=True)
    current_version_number = models.PositiveIntegerField(default=1)
    executive_summary = models.TextField(blank=True, default="")
    metrics_snapshot = models.JSONField(default=dict, blank=True)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prepared_reports",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_reports",
    )
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_reports"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "reference_code"],
                name="uq_project_report_reference",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "project", "status"],
                name="idx_report_proj_status",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.template_id and self.organization_id:
            if self.template.organization_id != self.organization_id:
                raise ValidationError(
                    "ReportTemplate does not belong to the Report's Organization."
                )

    def __str__(self) -> str:
        return f"{self.reference_code}: {self.title} (v{self.current_version_number} - {self.status})"


class ReportVersion(ProjectScopedModel):
    """
    Versioned snapshot of a Report (Phase 2).
    Once approved/published, `is_immutable=True` locks the snapshot against tampering.
    """

    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="versions",
        db_index=True,
    )
    version_number = models.PositiveIntegerField()
    change_summary = models.CharField(max_length=500, blank=True, default="")
    content_snapshot = models.JSONField(default=dict, blank=True)
    export_pdf_url = models.URLField(max_length=1024, blank=True, default="")
    export_docx_url = models.URLField(max_length=1024, blank=True, default="")
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    is_immutable = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_report_versions",
    )

    class Meta:
        db_table = "civora_report_versions"
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "version_number"],
                name="uq_report_version_number",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if self.report_id and self.project_id:
            if self.report.project_id != self.project_id:
                raise ValidationError("ReportVersion project must match its parent Report project.")

    def __str__(self) -> str:
        return f"{self.report.reference_code} v{self.version_number}"


class ReportSection(ProjectScopedModel):
    """
    Modular narrative or tabular section within a Report and optional ReportVersion (Phase 2).
    """

    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="sections",
        db_index=True,
    )
    report_version = models.ForeignKey(
        ReportVersion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sections",
    )
    section_key = models.CharField(max_length=80)
    title = models.CharField(max_length=200)
    order_index = models.PositiveSmallIntegerField(default=0)
    content_markdown = models.TextField(blank=True, default="")
    structured_data = models.JSONField(default=dict, blank=True)
    ai_assisted = models.BooleanField(default=False)

    class Meta:
        db_table = "civora_report_sections"
        ordering = ["order_index", "created_at"]

    def clean(self) -> None:
        super().clean()
        if self.report_id and self.project_id:
            if self.report.project_id != self.project_id:
                raise ValidationError("ReportSection project must match its parent Report project.")

    def __str__(self) -> str:
        return f"{self.report.reference_code} §{self.order_index} {self.title}"


class ReportEvidenceLink(ProjectScopedModel):
    """
    Traceable citation linking a Report or ReportSection back to a verified field Submission
    and/or SubmissionAttachment (Phase 2).
    """

    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="evidence_links",
        db_index=True,
    )
    section = models.ForeignKey(
        ReportSection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evidence_links",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_links",
    )
    attachment = models.ForeignKey(
        "evidence.SubmissionAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_links",
    )
    citation_label = models.CharField(max_length=160, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_report_evidence_links",
    )

    class Meta:
        db_table = "civora_report_evidence_links"
        ordering = ["created_at"]

    def clean(self) -> None:
        super().clean()
        if self.report_id and self.project_id:
            if self.report.project_id != self.project_id:
                raise ValidationError("ReportEvidenceLink project must match its Report project.")
        if self.submission_id and self.project_id:
            if self.submission.project_id != self.project_id:
                raise ValidationError("Cited Submission does not belong to the Report project.")
        if self.attachment_id and self.project_id:
            if self.attachment.project_id != self.project_id:
                raise ValidationError("Cited SubmissionAttachment does not belong to the Report project.")

    def __str__(self) -> str:
        return f"Citation {self.citation_label or self.id} -> {self.report.reference_code}"
