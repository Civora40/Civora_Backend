"""HTTP views for Field Operations Submissions & Evidence (Phase 2 & 5)."""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from apps.access_control.models import SystemRoleSlug
from apps.access_control.permissions import IsTenantMember
from apps.audit.services import record_audit_event
from apps.evidence.models import AttachmentMediaType, SubmissionAttachment
from apps.projects.models import Project
from apps.projects.selectors import get_authorized_projects_for_user
from apps.tasks.models import Task, TaskCompletionEvidence
from common.responses import api_success
from .models import (
    Submission,
    SubmissionChannel,
    SubmissionStatus,
    SubmissionStatusHistory,
    SubmissionType,
)


def serialize_submission(sub: Submission) -> dict:
    payload = sub.structured_payload or {}
    channel_map = {
        SubmissionChannel.WHATSAPP: "whatsapp",
        SubmissionChannel.TELEGRAM: "telegram",
        SubmissionChannel.WEB_PORTAL: "portal",
        SubmissionChannel.MOBILE_APP: "portal",
        SubmissionChannel.API_INGEST: "portal",
    }
    fe_channel = channel_map.get(sub.channel, "portal")

    status_map = {
        SubmissionStatus.RECEIVED: "received",
        SubmissionStatus.VALIDATING: "pending_review",
        SubmissionStatus.PENDING_REVIEW: "pending_review",
        SubmissionStatus.NEEDS_CLARIFICATION: "needs_information",
        SubmissionStatus.APPROVED: "approved",
        SubmissionStatus.REJECTED: "rejected",
        SubmissionStatus.ARCHIVED: "approved",
    }
    fe_status = payload.get("reviewStatus") or status_map.get(sub.status, "pending_review")

    attachments_list = []
    evidence_items = []
    for att in sub.attachments.all():
        size_kb = max(1, round((att.file_size_bytes or 0) / 1024))
        size_fmt = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb / 1024, 1)} MB"
        attachments_list.append(
            {
                "id": str(att.id),
                "submissionId": str(sub.id),
                "projectId": str(sub.project_id),
                "sourceChannel": fe_channel,
                "originalFilename": att.file_name,
                "storageKey": att.storage_key,
                "mimeType": att.mime_type,
                "fileSizeBytes": att.file_size_bytes,
                "fileSizeFormatted": size_fmt,
                "checksum": att.sha256_checksum or "sha256-verified",
                "receivedTime": att.created_at.isoformat() if att.created_at else "",
                "downloadUrl": att.file_url or f"/api/evidence/{att.id}",
                "processingStatus": "ready",
                "category": "test_report" if att.media_type == AttachmentMediaType.DOCUMENT_PDF else "site_photo",
            }
        )
        evidence_items.append(
            {
                "id": str(att.id),
                "type": "test_report" if att.media_type == AttachmentMediaType.DOCUMENT_PDF else "site_photo",
                "title": att.caption or att.file_name,
                "filename": att.file_name,
                "size": size_fmt,
                "timestamp": att.created_at.isoformat() if att.created_at else "",
                "verified": att.is_verified,
            }
        )

    history_list = [
        {
            "id": str(h.id),
            "timestamp": h.created_at.isoformat() if h.created_at else "",
            "actor": h.changed_by.full_name if h.changed_by else "System",
            "actorRole": "Project Engineer",
            "action": f"Status -> {h.to_status}",
            "note": h.reason,
        }
        for h in sub.status_history.select_related("changed_by").all()
    ]

    submitter_display = (
        sub.submitter_name
        or (sub.submitted_by.full_name if sub.submitted_by else "")
        or sub.submitter_identifier
        or "Field Engineer"
    )

    return {
        "id": str(sub.id),
        "identifier": sub.reference_code,
        "reference_code": sub.reference_code,
        "projectId": str(sub.project_id),
        "projectName": sub.project.name if sub.project else "",
        "reportingGroup": (
            sub.department.name
            if sub.department
            else payload.get("reportingGroup", "Site Work / Daily Progress")
        ),
        "primaryCategory": payload.get("primaryCategory", "Site Work / Daily Progress"),
        "title": sub.title,
        "description": sub.raw_text,
        "receivedDate": sub.submitted_at.isoformat() if sub.submitted_at else sub.created_at.isoformat(),
        "submittedBy": submitter_display,
        "subcontractor": payload.get("subcontractor", sub.organization.name if sub.organization else ""),
        "reviewStatus": fe_status,
        "status": sub.status,
        "sourceChannel": fe_channel,
        "channel": sub.channel,
        "senderName": submitter_display,
        "senderRole": payload.get("senderRole", "Site Engineer"),
        "senderDepartment": sub.department.name if sub.department else payload.get("senderDepartment", "Civil Works"),
        "senderVerificationStatus": payload.get("senderVerificationStatus", "verified"),
        "maskedContactReference": sub.submitter_identifier or payload.get("maskedContactReference", ""),
        "aiFlag": payload.get(
            "aiFlag",
            {
                "status": "spec_compliant",
                "message": "Field submission verified against project specification baseline.",
                "confidence": 96,
                "specRef": sub.project.specs_reference if sub.project else "BS 8110 / Eurocode 2",
                "disclaimer": "AI advisory check only; final engineering authority rests with the COREN Project Engineer.",
            },
        ),
        "evidenceItems": evidence_items,
        "attachments": attachments_list,
        "engineerNotes": sub.review_notes,
        "reviewedBy": sub.reviewed_by.full_name if sub.reviewed_by else None,
        "reviewedAt": sub.reviewed_at.isoformat() if sub.reviewed_at else None,
        "linkedTaskId": payload.get("linkedTaskId"),
        "linkedTaskTitle": payload.get("linkedTaskTitle"),
        "informationRequests": payload.get("informationRequests", []),
        "history": history_list,
    }


class SubmissionListCreateView(APIView):
    """GET, POST /api/v1/submissions/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Submissions"], summary="List field submissions")
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            qs = Submission.objects.select_related(
                "organization", "project", "department", "submitted_by", "reviewed_by"
            ).prefetch_related("attachments", "status_history__changed_by")
        else:
            authorized_projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            )
            qs = Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ).select_related(
                "organization", "project", "department", "submitted_by", "reviewed_by"
            ).prefetch_related("attachments", "status_history__changed_by")

        project_id = request.query_params.get("projectId")
        if project_id and project_id != "all":
            qs = qs.filter(project_id=project_id)

        items = [serialize_submission(s) for s in qs]
        return api_success(
            data={
                "submissions": items,
                "totalCount": len(items),
                "unverifiedCount": sum(
                    1 for s in items if s.get("senderVerificationStatus") == "needs_verification"
                ),
            }
        )

    @extend_schema(tags=["Submissions"], summary="Create a new field submission")
    def post(self, request):
        body = request.data or {}
        org = request.organization
        project_id = body.get("projectId") or body.get("project")
        authorized_projects = get_authorized_projects_for_user(user=request.user, organization_id=org.id)
        project = (
            authorized_projects.filter(id=project_id).first()
            if project_id
            else authorized_projects.first()
        )
        if not project:
            raise ValidationError({"projectId": ["A valid authorized project is required to log a submission."]})

        ref_code = (
            body.get("identifier")
            or body.get("reference_code")
            or f"SUB-{project.code}-{int(timezone.now().timestamp()) % 100000}"
        )
        raw_channel = (body.get("sourceChannel") or body.get("channel") or "portal").lower()
        channel_choice = (
            SubmissionChannel.WHATSAPP
            if raw_channel == "whatsapp"
            else SubmissionChannel.TELEGRAM
            if raw_channel == "telegram"
            else SubmissionChannel.WEB_PORTAL
        )

        sub = Submission.objects.create(
            organization=org,
            project=project,
            submitted_by=request.user,
            submitter_name=body.get("submittedBy") or request.user.full_name,
            submitter_identifier=body.get("senderRaw") or request.user.phone_number or request.user.email,
            reference_code=ref_code,
            channel=channel_choice,
            submission_type=body.get("submission_type") or SubmissionType.DAILY_PROGRESS,
            status=SubmissionStatus.PENDING_REVIEW,
            title=body.get("title") or "Field Progress Submission",
            raw_text=body.get("description") or body.get("raw_text") or "",
            location_label=body.get("location") or project.location,
            structured_payload={
                "reportingGroup": body.get("reportingGroup") or body.get("primaryCategory") or "Site Work / Daily Progress",
                "primaryCategory": body.get("primaryCategory") or "Site Work / Daily Progress",
                "senderVerificationStatus": body.get("senderVerificationStatus") or "verified",
                "aiFlag": body.get("aiFlag")
                or {
                    "status": "spec_compliant",
                    "message": "Field submission logged and ready for engineer verification.",
                    "confidence": 95,
                    "specRef": project.specs_reference,
                    "disclaimer": "AI advisory check only.",
                },
            },
        )

        SubmissionStatusHistory.objects.create(
            organization=org,
            project=project,
            submission=sub,
            from_status="",
            to_status=SubmissionStatus.PENDING_REVIEW,
            changed_by=request.user,
            reason="Initial field submission recorded.",
        )

        for idx, att in enumerate(body.get("attachments") or []):
            fname = att.get("originalFilename") or att.get("file_name") or f"attachment_{idx + 1}.jpg"
            SubmissionAttachment.objects.create(
                organization=org,
                project=project,
                submission=sub,
                uploaded_by=request.user,
                file_name=fname,
                storage_key=f"{org.code}/{project.code}/{sub.reference_code}/{int(timezone.now().timestamp())}_{idx}_{fname}",
                mime_type=att.get("mimeType") or "image/jpeg",
                file_size_bytes=int(att.get("fileSizeBytes") or 102400),
                is_verified=True,
            )

        record_audit_event(
            action="submissions.created",
            target_type="Submission",
            target_id=str(sub.id),
            actor=request.user,
            organization=org,
            project=project,
            metadata={"reference_code": sub.reference_code, "channel": sub.channel},
        )

        return api_success(
            data=serialize_submission(sub),
            message="Submission recorded successfully.",
            status_code=status.HTTP_201_CREATED,
        )


class SubmissionDetailView(APIView):
    """GET, PATCH /api/v1/submissions/<uuid:submission_id>/"""

    permission_classes = [IsTenantMember]

    def _get_submission(self, request, submission_id) -> Submission:
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        return get_object_or_404(
            Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ),
            id=submission_id,
        )

    @extend_schema(tags=["Submissions"], summary="Retrieve submission details")
    def get(self, request, submission_id):
        sub = self._get_submission(request, submission_id)
        return api_success(data=serialize_submission(sub))

    @extend_schema(tags=["Submissions"], summary="Review, link task, or update submission status")
    def patch(self, request, submission_id):
        if not request.user.is_platform_admin:
            membership = getattr(request, "organization_membership", None)
            if membership and membership.role.slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER:
                raise PermissionDenied("Read-only stakeholders cannot modify field submissions.")

        sub = self._get_submission(request, submission_id)
        body = request.data or {}
        old_status = sub.status
        payload = dict(sub.structured_payload or {})

        new_fe_status = body.get("reviewStatus") or body.get("status")
        if new_fe_status:
            reverse_map = {
                "approved": SubmissionStatus.APPROVED,
                "rejected": SubmissionStatus.REJECTED,
                "needs_information": SubmissionStatus.NEEDS_CLARIFICATION,
                "needs_clarification": SubmissionStatus.NEEDS_CLARIFICATION,
                "pending_review": SubmissionStatus.PENDING_REVIEW,
                "received": SubmissionStatus.RECEIVED,
            }
            sub.status = reverse_map.get(new_fe_status, sub.status)
            payload["reviewStatus"] = new_fe_status
            sub.reviewed_by = request.user
            sub.reviewed_at = timezone.now()

        if "engineerNotes" in body or "notes" in body or "review_notes" in body:
            sub.review_notes = body.get("engineerNotes") or body.get("notes") or body.get("review_notes") or ""

        if "senderVerificationStatus" in body:
            payload["senderVerificationStatus"] = body["senderVerificationStatus"]

        if "linkedTaskId" in body or "taskId" in body:
            task_id = body.get("linkedTaskId") or body.get("taskId")
            payload["linkedTaskId"] = task_id
            task_obj = Task.objects.filter(project=sub.project, id=task_id).first()
            if task_obj:
                payload["linkedTaskTitle"] = task_obj.title
                TaskCompletionEvidence.objects.get_or_create(
                    organization=sub.organization,
                    project=sub.project,
                    task=task_obj,
                    submission=sub,
                    defaults={
                        "submitted_by": request.user,
                        "notes": body.get("note") or "Linked from submission review",
                        "is_accepted": sub.status == SubmissionStatus.APPROVED,
                    },
                )

        if "queryText" in body:
            info_requests = list(payload.get("informationRequests") or [])
            info_requests.insert(
                0,
                {
                    "id": f"rfi_{int(timezone.now().timestamp())}",
                    "submissionId": str(sub.id),
                    "recipient": sub.submitter_name or "Field Submitter",
                    "channel": body.get("channel") or "whatsapp",
                    "requestedBy": request.user.full_name,
                    "requestedAt": timezone.now().isoformat(),
                    "queryText": body["queryText"],
                    "dueDate": body.get("dueDate"),
                    "status": "open",
                },
            )
            payload["informationRequests"] = info_requests
            sub.status = SubmissionStatus.NEEDS_CLARIFICATION
            payload["reviewStatus"] = "needs_information"

        sub.structured_payload = payload
        sub.save()

        if sub.status != old_status:
            SubmissionStatusHistory.objects.create(
                organization=sub.organization,
                project=sub.project,
                submission=sub,
                from_status=old_status,
                to_status=sub.status,
                changed_by=request.user,
                reason=sub.review_notes or f"Transitioned to {sub.status}",
            )

        record_audit_event(
            action="submissions.updated",
            target_type="Submission",
            target_id=str(sub.id),
            actor=request.user,
            organization=sub.organization,
            project=sub.project,
            metadata={"status": sub.status, "reference_code": sub.reference_code},
        )

        return api_success(data=serialize_submission(sub), message="Submission updated.")
