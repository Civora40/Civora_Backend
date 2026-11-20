"""Field Submissions Engine domain services (Phase 5)."""
from __future__ import annotations

from typing import List, Optional
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.access_control.services import enforce_project_permission
from apps.audit.services import record_audit_event
from apps.evidence.models import SubmissionAttachment
from apps.evidence.services import save_evidence_file
from apps.projects.services import ensure_project_allows_field_submissions
from apps.tasks.models import Task, TaskCompletionEvidence

from .models import (
    Submission,
    SubmissionChannel,
    SubmissionStatus,
    SubmissionStatusHistory,
    SubmissionType,
)

ALLOWED_SUBMISSION_TRANSITIONS = {
    SubmissionStatus.RECEIVED: {
        SubmissionStatus.VALIDATING,
        SubmissionStatus.PENDING_REVIEW,
        SubmissionStatus.REJECTED,
    },
    SubmissionStatus.VALIDATING: {
        SubmissionStatus.PENDING_REVIEW,
        SubmissionStatus.NEEDS_CLARIFICATION,
        SubmissionStatus.REJECTED,
    },
    SubmissionStatus.PENDING_REVIEW: {
        SubmissionStatus.APPROVED,
        SubmissionStatus.NEEDS_CLARIFICATION,
        SubmissionStatus.REJECTED,
    },
    SubmissionStatus.NEEDS_CLARIFICATION: {
        SubmissionStatus.PENDING_REVIEW,
        SubmissionStatus.VALIDATING,
        SubmissionStatus.REJECTED,
    },
    SubmissionStatus.APPROVED: {
        SubmissionStatus.ARCHIVED,
    },
    SubmissionStatus.REJECTED: {
        SubmissionStatus.PENDING_REVIEW,
        SubmissionStatus.ARCHIVED,
    },
    SubmissionStatus.ARCHIVED: set(),  # Terminal state
}


@transaction.atomic
def transition_submission_status(
    *,
    submission: Submission,
    to_status: str,
    actor,
    reason: str = "",
) -> Submission:
    """
    Transition a Submission to a new lifecycle state according to ALLOWED_SUBMISSION_TRANSITIONS.
    Enforces project verification capabilities, updates review metadata, and records immutable history.
    """
    enforce_project_permission(
        user=actor,
        organization=submission.organization,
        project=submission.project,
        permission_codename="submissions.verify",
        target_type="Submission",
        target_id=str(submission.id),
    )

    valid_choices = [c[0] for c in SubmissionStatus.choices]
    if to_status not in valid_choices:
        raise ValidationError(
            {"status": [f"'{to_status}' is not a valid submission status. Choices: {valid_choices}"]}
        )

    current_status = submission.status
    if current_status == to_status:
        return submission

    allowed = ALLOWED_SUBMISSION_TRANSITIONS.get(current_status, set())
    if to_status not in allowed:
        raise ValidationError(
            {
                "status": [
                    f"Illegal transition from '{current_status}' to '{to_status}'. "
                    f"Permitted next states: {sorted(list(allowed)) or 'None (terminal)'}."
                ]
            }
        )

    submission.status = to_status
    if reason:
        submission.review_notes = reason

    payload = dict(submission.structured_payload or {})
    fe_status_map = {
        SubmissionStatus.APPROVED: "approved",
        SubmissionStatus.REJECTED: "rejected",
        SubmissionStatus.NEEDS_CLARIFICATION: "needs_information",
        SubmissionStatus.PENDING_REVIEW: "pending_review",
        SubmissionStatus.VALIDATING: "pending_review",
        SubmissionStatus.RECEIVED: "received",
        SubmissionStatus.ARCHIVED: "approved",
    }
    payload["reviewStatus"] = fe_status_map.get(to_status, to_status)
    submission.structured_payload = payload

    if to_status in (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED, SubmissionStatus.NEEDS_CLARIFICATION):
        submission.reviewed_by = actor
        submission.reviewed_at = timezone.now()

    submission.save()

    SubmissionStatusHistory.objects.create(
        organization=submission.organization,
        project=submission.project,
        submission=submission,
        from_status=current_status,
        to_status=to_status,
        changed_by=actor,
        reason=reason or f"Status transitioned to {to_status}",
    )

    record_audit_event(
        action="submissions.status_transitioned",
        target_type="Submission",
        target_id=str(submission.id),
        actor=actor,
        organization=submission.organization,
        project=submission.project,
        metadata={
            "reference_code": submission.reference_code,
            "from_status": current_status,
            "to_status": to_status,
            "reason": reason,
        },
    )

    return submission


@transaction.atomic
def link_submission_to_task(
    *,
    submission: Submission,
    task: Task,
    actor,
    notes: str = "",
) -> TaskCompletionEvidence:
    """Link a Submission to a Task as completion evidence."""
    if task.project_id != submission.project_id:
        raise ValidationError(
            {"taskId": ["Target task does not belong to the submission project."]}
        )

    enforce_project_permission(
        user=actor,
        organization=submission.organization,
        project=submission.project,
        permission_codename="submissions.verify",
        target_type="Submission",
        target_id=str(submission.id),
    )

    evidence, created = TaskCompletionEvidence.objects.get_or_create(
        organization=submission.organization,
        project=submission.project,
        task=task,
        submission=submission,
        defaults={
            "submitted_by": actor,
            "notes": notes or f"Linked from submission {submission.reference_code}",
            "is_accepted": submission.status == SubmissionStatus.APPROVED,
        },
    )
    if not created and notes:
        evidence.notes = notes
        evidence.save(update_fields=["notes", "updated_at"])

    payload = dict(submission.structured_payload or {})
    payload["linkedTaskId"] = str(task.id)
    payload["linkedTaskTitle"] = task.title
    submission.structured_payload = payload
    submission.save(update_fields=["structured_payload", "updated_at"])

    record_audit_event(
        action="submissions.task_linked",
        target_type="Submission",
        target_id=str(submission.id),
        actor=actor,
        organization=submission.organization,
        project=submission.project,
        metadata={
            "task_id": str(task.id),
            "task_code": task.code,
            "reference_code": submission.reference_code,
        },
    )

    return evidence


@transaction.atomic
def add_submission_rfi(
    *,
    submission: Submission,
    query_text: str,
    actor,
    channel: str = "whatsapp",
    due_date: Optional[str] = None,
) -> dict:
    """Issue a formal RFI / clarification request to the field submitter."""
    enforce_project_permission(
        user=actor,
        organization=submission.organization,
        project=submission.project,
        permission_codename="submissions.verify",
        target_type="Submission",
        target_id=str(submission.id),
    )

    if not query_text:
        raise ValidationError({"queryText": ["Query text is required for an information request."]})

    payload = dict(submission.structured_payload or {})
    requests_list = list(payload.get("informationRequests") or [])

    rfi_id = f"rfi_{int(timezone.now().timestamp())}"
    rfi_entry = {
        "id": rfi_id,
        "submissionId": str(submission.id),
        "recipient": submission.submitter_name or submission.submitter_identifier or "Field Submitter",
        "channel": channel or "whatsapp",
        "requestedBy": actor.full_name or actor.email,
        "requestedAt": timezone.now().isoformat(),
        "queryText": query_text,
        "dueDate": due_date,
        "status": "open",
    }
    requests_list.insert(0, rfi_entry)
    payload["informationRequests"] = requests_list
    payload["reviewStatus"] = "needs_information"
    submission.structured_payload = payload

    old_status = submission.status
    submission.status = SubmissionStatus.NEEDS_CLARIFICATION
    submission.reviewed_by = actor
    submission.reviewed_at = timezone.now()
    submission.review_notes = f"Clarification requested: {query_text}"
    submission.save()

    SubmissionStatusHistory.objects.create(
        organization=submission.organization,
        project=submission.project,
        submission=submission,
        from_status=old_status,
        to_status=SubmissionStatus.NEEDS_CLARIFICATION,
        changed_by=actor,
        reason=f"Clarification requested: {query_text}",
    )

    record_audit_event(
        action="submissions.rfi_created",
        target_type="Submission",
        target_id=str(submission.id),
        actor=actor,
        organization=submission.organization,
        project=submission.project,
        metadata={"rfi_id": rfi_id, "query_text": query_text, "channel": channel},
    )

    return rfi_entry
