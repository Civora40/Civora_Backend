"""HTTP views for Evidence media & attachments (Phase 5)."""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.access_control.permissions import IsTenantMember
from apps.access_control.services import (
    enforce_organization_permission,
    enforce_project_permission,
)
from apps.audit.services import record_audit_event
from apps.projects.selectors import get_authorized_projects_for_user
from apps.projects.services import ensure_project_allows_field_submissions
from apps.submissions.models import Submission
from common.responses import api_success

from .models import SubmissionAttachment
from .serializers import (
    EvidenceUploadSerializer,
    SubmissionAttachmentSerializer,
    VerifyEvidenceSerializer,
)
from .services import (
    save_evidence_file,
    serialize_attachment,
    stream_evidence_attachment,
    verify_attachment,
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


class EvidenceListView(APIView):
    """GET /api/v1/evidence/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Evidence"], summary="List evidence attachments across authorized projects")
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            qs = SubmissionAttachment.objects.select_related(
                "organization", "project", "submission", "uploaded_by"
            )
        else:
            enforce_organization_permission(
                user=request.user,
                organization=request.organization,
                permission_codename="submissions.view",
                target_type="SubmissionAttachment",
            )
            authorized_projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            )
            qs = SubmissionAttachment.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ).select_related("organization", "project", "submission", "uploaded_by")

        # Filters
        project_id = request.query_params.get("projectId") or request.query_params.get("project")
        if project_id and project_id != "all":
            qs = qs.filter(project_id=project_id)

        submission_id = request.query_params.get("submissionId") or request.query_params.get("submission")
        if submission_id:
            qs = qs.filter(submission_id=submission_id)

        media_type = request.query_params.get("mediaType") or request.query_params.get("media_type")
        if media_type and media_type != "all":
            qs = qs.filter(media_type=media_type)

        is_verified = request.query_params.get("isVerified") or request.query_params.get("is_verified")
        if is_verified is not None and is_verified != "":
            val = is_verified.lower() in ("true", "1", "yes")
            qs = qs.filter(is_verified=val)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(file_name__icontains=search)
                | Q(caption__icontains=search)
                | Q(sha256_checksum__icontains=search)
                | Q(submission__reference_code__icontains=search)
            )

        items = [serialize_attachment(att) for att in qs]
        return api_success(
            data={
                "evidence": items,
                "totalCount": len(items),
                "verifiedCount": sum(1 for item in items if item.get("isVerified")),
            }
        )


class EvidenceUploadView(APIView):
    """POST /api/v1/evidence/upload/"""

    permission_classes = [IsTenantMember]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(tags=["Evidence"], summary="Upload an evidence attachment file")
    def post(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            raise ValidationError({"file": ["An uploaded file is required."]})

        project_id = (
            request.data.get("projectId")
            or request.data.get("project")
            or request.POST.get("projectId")
            or request.POST.get("project")
        )
        submission_id = (
            request.data.get("submissionId")
            or request.data.get("submission")
            or request.POST.get("submissionId")
            or request.POST.get("submission")
        )

        org = request.organization
        authorized_projects = get_authorized_projects_for_user(user=request.user, organization_id=org.id)

        project = None
        if project_id:
            project = authorized_projects.filter(id=project_id).first()
        elif submission_id:
            sub = Submission.objects.filter(id=submission_id, organization=org).first()
            if sub and authorized_projects.filter(id=sub.project_id).exists():
                project = sub.project

        if not project:
            raise ValidationError({"projectId": ["A valid authorized project is required to upload evidence."]})

        enforce_project_permission(
            user=request.user,
            organization=org,
            project=project,
            permission_codename="submissions.create",
            target_type="SubmissionAttachment",
        )
        ensure_project_allows_field_submissions(project)

        submission = None
        if submission_id:
            submission = Submission.objects.filter(id=submission_id, project=project).first()

        caption = request.data.get("caption") or request.POST.get("caption") or ""
        captured_at = request.data.get("capturedAt") or request.POST.get("capturedAt")
        gps_lat = normalize_coordinate(
            request.data.get("gpsLatitude") or request.POST.get("gpsLatitude")
        )
        gps_lon = normalize_coordinate(
            request.data.get("gpsLongitude") or request.POST.get("gpsLongitude")
        )
        media_type = request.data.get("mediaType") or request.POST.get("mediaType")

        attachment = save_evidence_file(
            file_obj=file_obj,
            organization=org,
            project=project,
            submission=submission,
            uploaded_by=request.user,
            caption=caption,
            captured_at=captured_at,
            gps_latitude=gps_lat,
            gps_longitude=gps_lon,
            media_type=media_type,
            is_verified=False,
        )

        record_audit_event(
            action="evidence.uploaded",
            target_type="SubmissionAttachment",
            target_id=str(attachment.id),
            actor=request.user,
            organization=org,
            project=project,
            metadata={
                "file_name": attachment.file_name,
                "sha256_checksum": attachment.sha256_checksum,
                "submission_id": str(submission.id) if submission else None,
            },
        )

        return api_success(
            data=serialize_attachment(attachment),
            message="Evidence attachment uploaded successfully.",
            status_code=status.HTTP_201_CREATED,
        )


class EvidenceDetailView(APIView):
    """GET, PATCH, DELETE /api/v1/evidence/<uuid:attachment_id>/"""

    permission_classes = [IsTenantMember]

    def _get_attachment(self, request, attachment_id) -> SubmissionAttachment:
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            return get_object_or_404(SubmissionAttachment, id=attachment_id)

        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        return get_object_or_404(
            SubmissionAttachment.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ),
            id=attachment_id,
        )

    @extend_schema(tags=["Evidence"], summary="Retrieve evidence attachment metadata")
    def get(self, request, attachment_id):
        att = self._get_attachment(request, attachment_id)
        enforce_project_permission(
            user=request.user,
            organization=att.organization,
            project=att.project,
            permission_codename="submissions.view",
            target_type="SubmissionAttachment",
            target_id=str(att.id),
        )
        return api_success(data=serialize_attachment(att))

    @extend_schema(tags=["Evidence"], summary="Update evidence attachment metadata")
    def patch(self, request, attachment_id):
        att = self._get_attachment(request, attachment_id)
        enforce_project_permission(
            user=request.user,
            organization=att.organization,
            project=att.project,
            permission_codename="submissions.verify",
            target_type="SubmissionAttachment",
            target_id=str(att.id),
        )
        body = request.data or {}
        if "caption" in body:
            att.caption = body["caption"]
        if "gpsLatitude" in body:
            att.gps_latitude = normalize_coordinate(body["gpsLatitude"])
        if "gpsLongitude" in body:
            att.gps_longitude = normalize_coordinate(body["gpsLongitude"])
        att.save()

        record_audit_event(
            action="evidence.updated",
            target_type="SubmissionAttachment",
            target_id=str(att.id),
            actor=request.user,
            organization=att.organization,
            project=att.project,
            metadata={"file_name": att.file_name},
        )
        return api_success(data=serialize_attachment(att), message="Attachment metadata updated.")

    @extend_schema(tags=["Evidence"], summary="Delete evidence attachment")
    def delete(self, request, attachment_id):
        att = self._get_attachment(request, attachment_id)
        enforce_project_permission(
            user=request.user,
            organization=att.organization,
            project=att.project,
            permission_codename="submissions.verify",
            target_type="SubmissionAttachment",
            target_id=str(att.id),
        )
        file_name = att.file_name
        att.delete()

        record_audit_event(
            action="evidence.deleted",
            target_type="SubmissionAttachment",
            target_id=str(attachment_id),
            actor=request.user,
            organization=request.organization,
            project=att.project,
            metadata={"file_name": file_name},
        )
        return api_success(message="Evidence attachment removed.", status_code=status.HTTP_200_OK)


class EvidenceDownloadView(APIView):
    """GET /api/v1/evidence/<uuid:attachment_id>/download/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Evidence"], summary="Stream/download evidence attachment with authorization check")
    def get(self, request, attachment_id):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            att = get_object_or_404(SubmissionAttachment, id=attachment_id)
        else:
            authorized_projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            )
            att = get_object_or_404(
                SubmissionAttachment.objects.filter(
                    organization=request.organization,
                    project__in=authorized_projects,
                ),
                id=attachment_id,
            )

        enforce_project_permission(
            user=request.user,
            organization=att.organization,
            project=att.project,
            permission_codename="submissions.view",
            target_type="SubmissionAttachment",
            target_id=str(att.id),
        )

        return stream_evidence_attachment(att)


class EvidenceVerifyView(APIView):
    """POST /api/v1/evidence/<uuid:attachment_id>/verify/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Evidence"], summary="Verify or certify an evidence attachment")
    def post(self, request, attachment_id):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            att = get_object_or_404(SubmissionAttachment, id=attachment_id)
        else:
            authorized_projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            )
            att = get_object_or_404(
                SubmissionAttachment.objects.filter(
                    organization=request.organization,
                    project__in=authorized_projects,
                ),
                id=attachment_id,
            )

        serializer = VerifyEvidenceSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        is_verified = serializer.validated_data.get("is_verified", True)
        notes = serializer.validated_data.get("notes", "")

        updated_att = verify_attachment(
            attachment=att,
            actor=request.user,
            is_verified=is_verified,
            notes=notes,
        )

        return api_success(
            data=serialize_attachment(updated_att),
            message="Evidence attachment verification updated.",
        )
