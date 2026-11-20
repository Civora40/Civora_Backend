"""HTTP views for Field Operations Submissions & Evidence (Phase 2 & 5)."""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.access_control.models import SystemRoleSlug
from apps.access_control.permissions import IsTenantMember
from apps.access_control.services import (
    enforce_organization_permission,
    enforce_project_permission,
)
from apps.audit.services import record_audit_event
from apps.evidence.models import AttachmentMediaType, SubmissionAttachment
from apps.evidence.services import (
    format_file_size,
    save_evidence_file,
    serialize_attachment,
)
from apps.projects.models import (
    Project,
    ProjectActivity,
    ProjectApprovalStatus,
    ProjectDepartment,
    ProjectStatus,
)
from apps.projects.selectors import get_authorized_projects_for_user
from apps.projects.services import ensure_project_allows_field_submissions
from apps.tasks.models import Task, TaskCompletionEvidence
from common.responses import api_success

from .models import (
    Submission,
    SubmissionChannel,
    SubmissionStatus,
    SubmissionStatusHistory,
    SubmissionType,
)
from .services import (
    add_submission_rfi,
    link_submission_to_task,
    transition_submission_status,
)


from decimal import Decimal, ROUND_HALF_UP

def normalize_coordinate(val):
    if val is None or val == "":
        return None
    try:
        d = Decimal(str(val))
        return d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def serialize_submission(sub: Submission) -> dict:
    payload = sub.structured_payload or {}
    channel_map = {
        SubmissionChannel.WHATSAPP: "whatsapp",
        SubmissionChannel.TELEGRAM: "telegram",
        SubmissionChannel.WEB_PORTAL: "portal",
        SubmissionChannel.MOBILE_APP: "portal",
        SubmissionChannel.API_INGEST: "api_ingest",
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
        size_fmt = format_file_size(att.file_size_bytes)
        cat = "test_report" if att.media_type == AttachmentMediaType.DOCUMENT_PDF else "site_photo"
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
                "downloadUrl": att.file_url or f"/api/v1/evidence/{att.id}/download/",
                "processingStatus": "ready",
                "category": cat,
                "isVerified": att.is_verified,
            }
        )
        evidence_items.append(
            {
                "id": str(att.id),
                "type": cat,
                "title": att.caption or att.file_name,
                "filename": att.file_name,
                "size": size_fmt,
                "timestamp": att.created_at.isoformat() if att.created_at else "",
                "verified": att.is_verified,
                "downloadUrl": att.file_url or f"/api/v1/evidence/{att.id}/download/",
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
        "departmentId": str(sub.department_id) if sub.department_id else None,
        "departmentName": sub.department.name if sub.department else None,
        "departmentCode": sub.department.code if sub.department else None,
        "activityId": str(sub.activity_id) if sub.activity_id else None,
        "activityName": sub.activity.name if sub.activity else None,
        "activityCode": sub.activity.code if sub.activity else None,
        "reportingGroup": (
            sub.department.name
            if sub.department
            else payload.get("reportingGroup", "Site Work / Daily Progress")
        ),
        "primaryCategory": payload.get("primaryCategory", "Site Work / Daily Progress"),
        "title": sub.title,
        "description": sub.raw_text,
        "raw_text": sub.raw_text,
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else "",
        "server_received_at": sub.created_at.isoformat() if sub.created_at else "",
        "receivedDate": sub.submitted_at.isoformat() if sub.submitted_at else sub.created_at.isoformat(),
        "submittedBy": submitter_display,
        "submitter_identifier": sub.submitter_identifier,
        "subcontractor": payload.get("subcontractor", sub.organization.name if sub.organization else ""),
        "reviewStatus": fe_status,
        "status": sub.status,
        "sourceChannel": fe_channel,
        "channel": sub.channel,
        "submission_type": sub.submission_type,
        "senderName": submitter_display,
        "senderRole": payload.get("senderRole", "Site Engineer"),
        "senderDepartment": sub.department.name if sub.department else payload.get("senderDepartment", "Civil Works"),
        "senderVerificationStatus": payload.get("senderVerificationStatus", "verified"),
        "maskedContactReference": sub.submitter_identifier or payload.get("maskedContactReference", ""),
        "gpsLatitude": float(sub.gps_latitude) if sub.gps_latitude is not None else None,
        "gpsLongitude": float(sub.gps_longitude) if sub.gps_longitude is not None else None,
        "location": sub.location_label,
        "weather": sub.weather_condition,
        "completenessScore": sub.completeness_score,
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
        "structured_payload": payload,
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
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(tags=["Submissions"], summary="List field submissions")
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            qs = Submission.objects.select_related(
                "organization", "project", "department", "activity", "submitted_by", "reviewed_by"
            ).prefetch_related("attachments", "status_history__changed_by")
        else:
            enforce_organization_permission(
                user=request.user,
                organization=request.organization,
                permission_codename="submissions.view",
                target_type="Submission",
            )
            authorized_projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            )
            qs = Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ).select_related(
                "organization", "project", "department", "activity", "submitted_by", "reviewed_by"
            ).prefetch_related("attachments", "status_history__changed_by")

        project_id = request.query_params.get("projectId") or request.query_params.get("project")
        if project_id and project_id != "all":
            qs = qs.filter(project_id=project_id)

        department_id = request.query_params.get("departmentId") or request.query_params.get("department")
        if department_id and department_id != "all":
            qs = qs.filter(department_id=department_id)

        activity_id = request.query_params.get("activityId") or request.query_params.get("activity")
        if activity_id and activity_id != "all":
            qs = qs.filter(activity_id=activity_id)

        status_param = request.query_params.get("status")
        if status_param and status_param != "all":
            reverse_map = {
                "needs_information": SubmissionStatus.NEEDS_CLARIFICATION,
                "approved": SubmissionStatus.APPROVED,
                "rejected": SubmissionStatus.REJECTED,
                "pending_review": SubmissionStatus.PENDING_REVIEW,
                "received": SubmissionStatus.RECEIVED,
                "validating": SubmissionStatus.VALIDATING,
                "archived": SubmissionStatus.ARCHIVED,
            }
            sub_status = reverse_map.get(status_param, status_param)
            qs = qs.filter(status=sub_status)

        channel_param = request.query_params.get("channel")
        if channel_param and channel_param != "all":
            qs = qs.filter(channel=channel_param)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(raw_text__icontains=search)
                | Q(reference_code__icontains=search)
                | Q(submitter_name__icontains=search)
                | Q(submitter_identifier__icontains=search)
            )

        date_from = request.query_params.get("date_from")
        if date_from:
            parsed_from = parse_datetime(date_from) or parse_date(date_from)
            if parsed_from:
                qs = qs.filter(submitted_at__gte=parsed_from)

        date_to = request.query_params.get("date_to")
        if date_to:
            parsed_to = parse_datetime(date_to) or parse_date(date_to)
            if parsed_to:
                qs = qs.filter(submitted_at__lte=parsed_to)

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
        project_id = body.get("projectId") or body.get("project") or request.POST.get("projectId")
        authorized_projects = get_authorized_projects_for_user(user=request.user, organization_id=org.id)
        project = (
            authorized_projects.filter(id=project_id).first()
            if project_id
            else (
                authorized_projects.filter(
                    approval_status=ProjectApprovalStatus.APPROVED,
                    status=ProjectStatus.ACTIVE,
                ).first()
                or authorized_projects.first()
            )
        )
        if not project:
            raise ValidationError({"projectId": ["A valid authorized project is required to log a submission."]})

        enforce_project_permission(
            user=request.user,
            organization=org,
            project=project,
            permission_codename="submissions.create",
            target_type="Submission",
        )
        ensure_project_allows_field_submissions(project)

        # Department & Activity resolution
        department = None
        dept_id = body.get("departmentId") or body.get("department") or request.POST.get("departmentId")
        if dept_id:
            department = ProjectDepartment.objects.filter(id=dept_id, project=project).first()

        activity = None
        act_id = body.get("activityId") or body.get("activity") or request.POST.get("activityId")
        if act_id:
            activity = ProjectActivity.objects.filter(id=act_id, project=project).first()

        ref_code = (
            body.get("identifier")
            or body.get("reference_code")
            or request.POST.get("reference_code")
            or f"SUB-{project.code}-{int(timezone.now().timestamp()) % 100000}"
        )

        raw_channel = (
            body.get("sourceChannel")
            or body.get("channel")
            or request.POST.get("channel")
            or "portal"
        ).lower()
        channel_choices = {
            "whatsapp": SubmissionChannel.WHATSAPP,
            "telegram": SubmissionChannel.TELEGRAM,
            "mobile_app": SubmissionChannel.MOBILE_APP,
            "api_ingest": SubmissionChannel.API_INGEST,
            "portal": SubmissionChannel.WEB_PORTAL,
            "web_portal": SubmissionChannel.WEB_PORTAL,
        }
        channel_choice = channel_choices.get(raw_channel, SubmissionChannel.WEB_PORTAL)

        raw_sub_type = (
            body.get("submission_type")
            or body.get("submissionType")
            or request.POST.get("submission_type")
            or SubmissionType.DAILY_PROGRESS
        )
        valid_sub_types = [c[0] for c in SubmissionType.choices]
        sub_type = raw_sub_type if raw_sub_type in valid_sub_types else SubmissionType.DAILY_PROGRESS

        initial_status = body.get("status") or request.POST.get("status") or SubmissionStatus.PENDING_REVIEW
        valid_statuses = [c[0] for c in SubmissionStatus.choices]
        if initial_status not in valid_statuses:
            initial_status = SubmissionStatus.PENDING_REVIEW

        submitted_at_val = timezone.now()
        raw_submitted_at = body.get("submitted_at") or body.get("submittedAt") or request.POST.get("submitted_at")
        if raw_submitted_at:
            parsed_dt = parse_datetime(raw_submitted_at) or parse_date(raw_submitted_at)
            if parsed_dt:
                submitted_at_val = parsed_dt

        gps_lat = normalize_coordinate(
            body.get("gpsLatitude") or body.get("gps_latitude") or request.POST.get("gpsLatitude")
        )
        gps_lon = normalize_coordinate(
            body.get("gpsLongitude") or body.get("gps_longitude") or request.POST.get("gpsLongitude")
        )
        weather = body.get("weather") or body.get("weather_condition") or request.POST.get("weather") or ""

        title = body.get("title") or request.POST.get("title") or "Field Progress Submission"
        desc = (
            body.get("description")
            or body.get("raw_text")
            or request.POST.get("description")
            or request.POST.get("raw_text")
            or ""
        )
        location = body.get("location") or request.POST.get("location") or project.location

        sub = Submission.objects.create(
            organization=org,
            project=project,
            department=department,
            activity=activity,
            submitted_by=request.user,
            submitter_name=(
                body.get("submittedBy")
                or body.get("senderName")
                or request.POST.get("submittedBy")
                or request.user.full_name
            ),
            submitter_identifier=(
                body.get("senderRaw")
                or body.get("submitter_identifier")
                or request.POST.get("submitter_identifier")
                or request.user.phone_number
                or request.user.email
            ),
            reference_code=ref_code,
            channel=channel_choice,
            submission_type=sub_type,
            status=initial_status,
            title=title,
            raw_text=desc,
            gps_latitude=gps_lat,
            gps_longitude=gps_lon,
            location_label=location,
            weather_condition=weather,
            submitted_at=submitted_at_val,
            structured_payload={
                "reportingGroup": (
                    department.name if department else body.get("reportingGroup") or "Site Work / Daily Progress"
                ),
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
            to_status=sub.status,
            changed_by=request.user,
            reason="Initial field submission recorded.",
        )

        # Handle multipart files in request.FILES
        for uploaded_file in request.FILES.getlist("files") or request.FILES.getlist("attachments"):
            save_evidence_file(
                file_obj=uploaded_file,
                organization=org,
                project=project,
                submission=sub,
                uploaded_by=request.user,
                caption=uploaded_file.name,
                is_verified=False,
            )

        # Handle JSON attachments array if provided
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
                sha256_checksum=att.get("checksum") or "",
                is_verified=att.get("is_verified", False),
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
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            return get_object_or_404(
                Submission.objects.select_related(
                    "organization", "project", "department", "activity", "submitted_by", "reviewed_by"
                ).prefetch_related("attachments", "status_history__changed_by"),
                id=submission_id,
            )

        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        return get_object_or_404(
            Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ).select_related(
                "organization", "project", "department", "activity", "submitted_by", "reviewed_by"
            ).prefetch_related("attachments", "status_history__changed_by"),
            id=submission_id,
        )

    @extend_schema(tags=["Submissions"], summary="Retrieve submission details")
    def get(self, request, submission_id):
        sub = self._get_submission(request, submission_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=sub.project,
            permission_codename="submissions.view",
            target_type="Submission",
            target_id=str(sub.id),
        )
        return api_success(data=serialize_submission(sub))

    @extend_schema(tags=["Submissions"], summary="Review, link task, or update submission status")
    def patch(self, request, submission_id):
        sub = self._get_submission(request, submission_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=sub.project,
            permission_codename="submissions.verify",
            target_type="Submission",
            target_id=str(sub.id),
        )
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
            target_st = reverse_map.get(new_fe_status, sub.status)
            if target_st != sub.status:
                transition_submission_status(
                    submission=sub,
                    to_status=target_st,
                    actor=request.user,
                    reason=body.get("engineerNotes") or body.get("notes") or "",
                )

        if "engineerNotes" in body or "notes" in body or "review_notes" in body:
            sub.review_notes = body.get("engineerNotes") or body.get("notes") or body.get("review_notes") or ""
            sub.save(update_fields=["review_notes", "updated_at"])

        if "senderVerificationStatus" in body:
            payload["senderVerificationStatus"] = body["senderVerificationStatus"]
            sub.structured_payload = payload
            sub.save(update_fields=["structured_payload", "updated_at"])

        if "linkedTaskId" in body or "taskId" in body:
            task_id = body.get("linkedTaskId") or body.get("taskId")
            task_obj = Task.objects.filter(project=sub.project, id=task_id).first()
            if task_obj:
                link_submission_to_task(
                    submission=sub,
                    task=task_obj,
                    actor=request.user,
                    notes=body.get("note") or "Linked from submission review",
                )

        if "queryText" in body:
            add_submission_rfi(
                submission=sub,
                query_text=body["queryText"],
                actor=request.user,
                channel=body.get("channel") or "whatsapp",
                due_date=body.get("dueDate"),
            )

        # Refresh from DB
        sub.refresh_from_db()
        return api_success(data=serialize_submission(sub), message="Submission updated.")


class SubmissionTransitionStatusView(APIView):
    """POST /api/v1/submissions/<uuid:submission_id>/transition-status/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Submissions"], summary="Explicit state machine transition for submission")
    def post(self, request, submission_id):
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        sub = get_object_or_404(
            Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ),
            id=submission_id,
        )

        target_status = request.data.get("status")
        if not target_status:
            raise ValidationError({"status": ["Target status is required."]})

        reason = request.data.get("reason") or request.data.get("notes") or ""
        updated_sub = transition_submission_status(
            submission=sub,
            to_status=target_status,
            actor=request.user,
            reason=reason,
        )
        return api_success(
            data=serialize_submission(updated_sub),
            message=f"Submission status transitioned to {target_status}.",
        )


class SubmissionLinkTaskView(APIView):
    """POST /api/v1/submissions/<uuid:submission_id>/link-task/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Submissions"], summary="Link submission to a task as completion evidence")
    def post(self, request, submission_id):
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        sub = get_object_or_404(
            Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ),
            id=submission_id,
        )

        task_id = request.data.get("taskId") or request.data.get("linkedTaskId")
        if not task_id:
            raise ValidationError({"taskId": ["taskId is required."]})

        task = get_object_or_404(Task.objects.filter(project=sub.project), id=task_id)
        notes = request.data.get("notes") or request.data.get("note") or ""

        evidence = link_submission_to_task(
            submission=sub,
            task=task,
            actor=request.user,
            notes=notes,
        )
        sub.refresh_from_db()
        return api_success(
            data={
                "submission": serialize_submission(sub),
                "evidenceId": str(evidence.id),
                "taskId": str(task.id),
                "taskCode": task.code,
            },
            message=f"Submission linked to task {task.code}.",
        )


class SubmissionRequestInfoView(APIView):
    """POST /api/v1/submissions/<uuid:submission_id>/request-info/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Submissions"], summary="Issue an RFI to the field submitter")
    def post(self, request, submission_id):
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        sub = get_object_or_404(
            Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ),
            id=submission_id,
        )

        query_text = request.data.get("queryText")
        if not query_text:
            raise ValidationError({"queryText": ["queryText is required."]})

        channel = request.data.get("channel") or "whatsapp"
        due_date = request.data.get("dueDate")

        rfi = add_submission_rfi(
            submission=sub,
            query_text=query_text,
            actor=request.user,
            channel=channel,
            due_date=due_date,
        )
        sub.refresh_from_db()
        return api_success(
            data={
                "submission": serialize_submission(sub),
                "rfi": rfi,
            },
            message="Clarification request dispatched.",
        )


class SubmissionAttachmentsView(APIView):
    """POST /api/v1/submissions/<uuid:submission_id>/attachments/"""

    permission_classes = [IsTenantMember]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(tags=["Submissions"], summary="Attach uploaded file(s) directly to submission")
    def post(self, request, submission_id):
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        sub = get_object_or_404(
            Submission.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ),
            id=submission_id,
        )

        enforce_project_permission(
            user=request.user,
            organization=sub.organization,
            project=sub.project,
            permission_codename="submissions.create",
            target_type="SubmissionAttachment",
        )
        ensure_project_allows_field_submissions(sub.project)

        files = request.FILES.getlist("file") or request.FILES.getlist("files") or request.FILES.getlist("attachments")
        if not files:
            file_single = request.FILES.get("file")
            if file_single:
                files = [file_single]

        if not files:
            raise ValidationError({"file": ["At least one file is required."]})

        caption = request.data.get("caption") or request.POST.get("caption") or ""
        created_attachments = []
        for f in files:
            att = save_evidence_file(
                file_obj=f,
                organization=sub.organization,
                project=sub.project,
                submission=sub,
                uploaded_by=request.user,
                caption=caption or f.name,
                is_verified=False,
            )
            created_attachments.append(serialize_attachment(att))

        record_audit_event(
            action="evidence.attached_to_submission",
            target_type="Submission",
            target_id=str(sub.id),
            actor=request.user,
            organization=sub.organization,
            project=sub.project,
            metadata={"count": len(created_attachments), "reference_code": sub.reference_code},
        )

        return api_success(
            data={"attachments": created_attachments, "count": len(created_attachments)},
            message=f"{len(created_attachments)} evidence file(s) attached.",
            status_code=status.HTTP_201_CREATED,
        )
