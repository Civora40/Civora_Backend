"""HTTP views for Governance Reviews, Comments, and Approvals (Phase 2 & 8)."""
import hashlib
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from apps.access_control.models import SystemRoleSlug
from apps.access_control.permissions import IsTenantMember
from apps.audit.services import record_audit_event
from apps.projects.selectors import get_authorized_projects_for_user
from apps.reports.models import Report, ReportStatus, ReportVersion
from common.responses import api_success
from .models import Approval, ReportReview, ReviewComment, ReviewDecision


def serialize_review_comment(c: ReviewComment) -> dict:
    return {
        "id": str(c.id),
        "reportId": str(c.review.report_id),
        "reviewId": str(c.review_id),
        "sectionId": str(c.section_id) if c.section_id else "general",
        "sectionTitle": c.section.title if c.section else "General Dossier Review",
        "authorName": c.author.full_name if c.author else "Reviewer",
        "authorRole": c.author.job_title if c.author and c.author.job_title else "Stakeholder / Reviewer",
        "content": c.body,
        "feedbackType": "comment",
        "createdAt": c.created_at.isoformat() if c.created_at else "",
        "status": "resolved" if c.is_resolved else "open",
        "isResolved": c.is_resolved,
    }


def serialize_approval(a: Approval) -> dict:
    return {
        "id": str(a.id),
        "reportId": str(a.report_id),
        "reportVersionId": str(a.report_version_id),
        "approverName": a.approver.full_name if a.approver else "Authorized Signatory",
        "approverRole": a.approver_role_slug,
        "approverCorenNumber": a.approver_coren_number,
        "organization": a.organization.name if a.organization else "",
        "status": "approved",
        "signOffDate": a.approved_at.isoformat() if a.approved_at else "",
        "digitalSignatureHash": a.signature_hash,
        "declarationAgreed": True,
        "notes": a.certification_statement,
    }


class ApprovalListCreateView(APIView):
    """GET, POST /api/v1/approvals/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Approvals"], summary="List report reviews, comments, and formal approvals")
    def get(self, request):
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        comments_qs = ReviewComment.objects.filter(
            organization=request.organization,
            project__in=authorized_projects,
        ).select_related("review", "section", "author")
        approvals_qs = Approval.objects.filter(
            organization=request.organization,
            project__in=authorized_projects,
        ).select_related("organization", "approver")

        report_id = request.query_params.get("reportId")
        if report_id and len(str(report_id)) == 36:
            comments_qs = comments_qs.filter(review__report_id=report_id)
            approvals_qs = approvals_qs.filter(report_id=report_id)

        return api_success(
            data={
                "comments": [serialize_review_comment(c) for c in comments_qs],
                "approvals": [serialize_approval(a) for a in approvals_qs],
            }
        )

    @extend_schema(tags=["Approvals"], summary="Submit a review comment or formal report sign-off approval")
    def post(self, request):
        body = request.data or {}
        org = request.organization
        authorized_projects = get_authorized_projects_for_user(user=request.user, organization_id=org.id)
        report_id = body.get("reportId")
        report = (
            Report.objects.filter(organization=org, project__in=authorized_projects, id=report_id).first()
            if report_id and len(str(report_id)) == 36
            else Report.objects.filter(organization=org, project__in=authorized_projects).first()
        )
        if not report:
            raise ValidationError({"reportId": ["A valid authorized Report is required."]})

        action = body.get("action") or ("approve" if body.get("declarationAgreed") or body.get("signatureHash") else "comment")
        latest_version = report.versions.first() or ReportVersion.objects.create(
            organization=org,
            project=report.project,
            report=report,
            version_number=report.current_version_number,
            change_summary="Auto-created version for review/approval.",
            created_by=request.user,
        )

        review, _ = ReportReview.objects.get_or_create(
            organization=org,
            project=report.project,
            report=report,
            report_version=latest_version,
            reviewer=request.user,
            defaults={
                "requested_by": report.prepared_by or request.user,
                "decision": ReviewDecision.APPROVED if action == "approve" else ReviewDecision.PENDING,
                "summary_notes": body.get("notes") or body.get("content") or "",
            },
        )

        if action == "comment":
            comment = ReviewComment.objects.create(
                organization=org,
                project=report.project,
                review=review,
                author=request.user,
                body=body.get("content") or body.get("body") or "Review observation logged.",
            )
            record_audit_event(
                action="approvals.comment_added",
                target_type="ReviewComment",
                target_id=str(comment.id),
                actor=request.user,
                organization=org,
                project=report.project,
                metadata={"report_id": str(report.id)},
            )
            return api_success(
                data=serialize_review_comment(comment),
                message="Review comment recorded.",
                status_code=status.HTTP_201_CREATED,
            )

        review.decision = ReviewDecision.APPROVED
        review.decided_at = timezone.now()
        review.summary_notes = body.get("notes") or review.summary_notes
        review.save()

        raw_sig = f"{report.id}:{latest_version.id}:{request.user.id}:{timezone.now().isoformat()}"
        sig_hash = body.get("digitalSignatureHash") or f"SHA256-{hashlib.sha256(raw_sig.encode()).hexdigest()[:24].upper()}"
        membership = getattr(request, "organization_membership", None)
        role_slug = membership.role.slug if membership else SystemRoleSlug.PROJECT_ENGINEER

        approval, _ = Approval.objects.get_or_create(
            report_version=latest_version,
            approver=request.user,
            defaults={
                "organization": org,
                "project": report.project,
                "report": report,
                "review": review,
                "approver_role_slug": role_slug,
                "approver_coren_number": request.user.professional_registration or "",
                "signature_hash": sig_hash,
                "certification_statement": body.get("notes") or "Certified & approved.",
            },
        )

        report.status = ReportStatus.PUBLISHED
        report.approved_by = request.user
        report.published_at = report.published_at or timezone.now()
        report.save()

        record_audit_event(
            action="approvals.signed_off",
            target_type="Approval",
            target_id=str(approval.id),
            actor=request.user,
            organization=org,
            project=report.project,
            metadata={"report_id": str(report.id), "signature_hash": sig_hash},
        )

        return api_success(
            data=serialize_approval(approval),
            message="Formal approval sign-off recorded.",
            status_code=status.HTTP_201_CREATED,
        )
