"""
Task execution & evidence models (Phase 2 — Database Architecture and Data Modelling):
Task, TaskAssignment, and TaskCompletionEvidence scoped to Organization and Project.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from common.models import ProjectScopedModel


class TaskStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    SUBMITTED_FOR_VERIFICATION = "submitted_for_verification", "Submitted for Verification"
    COMPLETED = "completed", "Completed & Verified"
    OVERDUE = "overdue", "Overdue"
    BLOCKED = "blocked", "Blocked"
    CANCELLED = "cancelled", "Cancelled"


class TaskPriority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class Task(ProjectScopedModel):
    """
    Engineering or field task scoped to a Project and optional Department/Activity (Phase 2).
    """

    code = models.CharField(max_length=60, db_index=True)
    department = models.ForeignKey(
        "projects.ProjectDepartment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    activity = models.ForeignKey(
        "projects.ProjectActivity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
        db_index=True,
    )
    priority = models.CharField(
        max_length=20,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
        db_index=True,
    )
    due_date = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    requires_evidence = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_tasks",
    )

    class Meta:
        db_table = "civora_tasks"
        ordering = ["due_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "code"],
                name="uq_project_task_code",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "project", "status"],
                name="idx_task_proj_status",
            ),
            models.Index(
                fields=["project", "due_date"],
                name="idx_task_proj_due_date",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.department_id and self.project_id:
            if self.department.project_id != self.project_id:
                raise ValidationError("Task department does not belong to the specified Project.")
        if self.activity_id and self.project_id:
            if self.activity.project_id != self.project_id:
                raise ValidationError("Task activity does not belong to the specified Project.")

    def __str__(self) -> str:
        return f"{self.code}: {self.title} ({self.status})"


class TaskAssignment(ProjectScopedModel):
    """
    Links a Task to an assigned User within the same Project (Phase 2).
    """

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="assignments",
        db_index=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_assignments",
        db_index=True,
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delegated_task_assignments",
    )
    role_in_task = models.CharField(
        max_length=60,
        default="owner",
        help_text="owner | collaborator | verifier",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_task_assignments"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "assignee"],
                name="uq_task_assignee",
            )
        ]
        indexes = [
            models.Index(
                fields=["project", "assignee", "is_active"],
                name="idx_task_assign_user_active",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.task_id and self.project_id:
            if self.task.project_id != self.project_id:
                raise ValidationError("TaskAssignment project must match its Task project.")

    def __str__(self) -> str:
        return f"{self.task.code} -> {self.assignee.email} ({self.role_in_task})"


class TaskCompletionEvidence(ProjectScopedModel):
    """
    Verifiable evidence linking a Task completion to a field Submission and/or Attachment (Phase 2).
    """

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="completion_evidence",
        db_index=True,
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_completions",
    )
    attachment = models.ForeignKey(
        "evidence.SubmissionAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_completions",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_task_evidence",
    )
    notes = models.TextField(blank=True, default="")
    is_accepted = models.BooleanField(default=False, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_task_evidence",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "civora_task_completion_evidence"
        ordering = ["-created_at"]

    def clean(self) -> None:
        super().clean()
        if self.task_id and self.project_id:
            if self.task.project_id != self.project_id:
                raise ValidationError("TaskCompletionEvidence project must match its Task project.")
        if self.submission_id and self.project_id:
            if self.submission.project_id != self.project_id:
                raise ValidationError("Linked Submission does not belong to the Task project.")
        if self.attachment_id and self.project_id:
            if self.attachment.project_id != self.project_id:
                raise ValidationError("Linked SubmissionAttachment does not belong to the Task project.")

    def __str__(self) -> str:
        return f"Evidence for {self.task.code} (accepted={self.is_accepted})"
