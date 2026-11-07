"""HTTP views for Reporting Engine & Templates (Phase 2, 7 & 8)."""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from apps.access_control.models import SystemRoleSlug
from apps.access_control.permissions import IsTenantMember
from apps.access_control.selectors import user_has_project_permission
from apps.access_control.services import (
    enforce_project_permission,
    enforce_report_approval_separation_of_duties,
)
from apps.audit.services import record_audit_event
from apps.projects.selectors import get_authorized_projects_for_user
from common.responses import api_success
from .models import (
    Report,
    ReportEvidenceLink,
    ReportSection,
    ReportStatus,
    ReportTemplate,
    ReportType,
    ReportVersion,
)


def serialize_report(report: Report) -> dict:
    status_map = {
        ReportStatus.DRAFT: "draft",
        ReportStatus.IN_REVIEW: "review",
        ReportStatus.CHANGES_REQUESTED: "draft",
        ReportStatus.APPROVED: "approved",
        ReportStatus.PUBLISHED: "released",
        ReportStatus.ARCHIVED: "released",
    }
    fe_status = status_map.get(report.status, "draft")
    start_str = report.reporting_period_start.isoformat() if report.reporting_period_start else "2026-10-01"
    end_str = report.reporting_period_end.isoformat() if report.reporting_period_end else "2026-10-31"
    period_fmt = f"{start_str} to {end_str}"

    sections_data = [
        {
            "id": str(s.id),
            "sectionKey": s.section_key,
            "title": s.title,
            "content": s.content_markdown,
            "orderIndex": s.order_index,
            "isRequired": True,
            "isCompleted": bool(s.content_markdown.strip()),
            "aiAssisted": s.ai_assisted,
        }
        for s in report.sections.all()
    ]

    versions_data = [
        {
            "id": str(v.id),
            "versionNumber": f"v{v.version_number}.0",
            "createdAt": v.created_at.isoformat() if v.created_at else "",
            "createdBy": v.created_by.full_name if v.created_by else "Project Engineer",
            "summary": v.change_summary,
            "isImmutable": v.is_immutable,
        }
        for v in report.versions.select_related("created_by").all()
    ]

    author_name = report.prepared_by.full_name if report.prepared_by else (
        report.project.lead_engineer_name if report.project else "Project Engineer"
    )
    approver_name = report.approved_by.full_name if report.approved_by else None

    return {
        "id": str(report.id),
        "reportNumber": report.reference_code,
        "reference_code": report.reference_code,
        "projectId": str(report.project_id),
        "projectName": report.project.name if report.project else "",
        "projectCode": report.project.code if report.project else "",
        "title": report.title,
        "reportType": report.get_report_type_display(),
        "status": fe_status,
        "workflowStatus": report.status,
        "period": period_fmt,
        "reportingPeriodStart": start_str,
        "reportingPeriodEnd": end_str,
        "periodFormatted": period_fmt,
        "generatedBy": author_name,
        "createdById": str(report.prepared_by_id) if report.prepared_by_id else "",
        "createdByName": author_name,
        "authorTitle": report.prepared_by.job_title if report.prepared_by and report.prepared_by.job_title else "Project Engineer",
        "approvedBy": approver_name,
        "releasedBy": approver_name if report.published_at else None,
        "releasedAt": report.published_at.isoformat() if report.published_at else None,
        "currentVersion": f"v{report.current_version_number}.0",
        "submissionCount": report.evidence_links.count() or (report.project.submissions_submission_records.count() if report.project else 0),
        "sourceSubmissionsCount": report.evidence_links.count(),
        "linkedEvidenceCount": report.evidence_links.count(),
        "flagsResolvedCount": 0,
        "executiveSummary": report.executive_summary,
        "sections": sections_data,
        "versions": versions_data,
        "lastSavedAt": report.updated_at.isoformat() if report.updated_at else "",
        "auditTrail": [
            {
                "id": f"aud_{report.id}",
                "action": f"Report {report.status}",
                "actor": author_name,
                "actorRole": "Project Engineer",
                "timestamp": report.updated_at.isoformat() if report.updated_at else "",
                "note": f"Current version v{report.current_version_number}.0",
            }
        ],
    }


def serialize_template(tpl: ReportTemplate) -> dict:
    return {
        "id": str(tpl.id),
        "name": tpl.name,
        "code": tpl.code,
        "category": tpl.report_type,
        "description": tpl.description,
        "sectionsCount": len(tpl.default_sections or []),
        "defaultSections": tpl.default_sections or [],
        "isSystemDefault": tpl.is_system_default,
        "status": "active" if tpl.is_active else "draft",
        "lastUpdated": tpl.updated_at.strftime("%Y-%m-%d") if tpl.updated_at else "",
    }


class ReportListCreateView(APIView):
    """GET, POST /api/v1/reports/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Reports"], summary="List project reports")
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            qs = Report.objects.select_related(
                "project", "prepared_by", "approved_by", "template"
            ).prefetch_related("sections", "versions__created_by", "evidence_links")
        else:
            authorized_projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            )
            qs = Report.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ).select_related(
                "project", "prepared_by", "approved_by", "template"
            ).prefetch_related("sections", "versions__created_by", "evidence_links")

            # Stakeholders see released/published/approved reports
            membership = getattr(request, "organization_membership", None)
            if (
                membership
                and membership.role.slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER
                and not request.user.is_platform_admin
            ):
                qs = qs.filter(status__in=[ReportStatus.APPROVED, ReportStatus.PUBLISHED])

        project_id = request.query_params.get("projectId")
        if project_id and project_id != "all":
            qs = qs.filter(project_id=project_id)

        reports_list = [serialize_report(r) for r in qs]
        return api_success(data={"reports": reports_list})

    @extend_schema(tags=["Reports"], summary="Create a new project report draft")
    def post(self, request):
        if not request.user.is_platform_admin:
            membership = getattr(request, "organization_membership", None)
            if membership and membership.role.slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER:
                raise PermissionDenied("Read-only stakeholders cannot author reports.")

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
            raise ValidationError({"projectId": ["A valid authorized project is required to create a report."]})

        ref_code = (
            body.get("reportNumber")
            or body.get("reference_code")
            or f"REP-{project.code}-{int(timezone.now().timestamp()) % 100000}"
        )

        report = Report.objects.create(
            organization=org,
            project=project,
            reference_code=ref_code,
            title=body.get("title") or f"{project.name} — Engineering Progress Report",
            report_type=body.get("report_type") or ReportType.WEEKLY_PROGRESS,
            status=ReportStatus.DRAFT,
            executive_summary=body.get("executiveSummary") or "",
            prepared_by=request.user,
        )

        version = ReportVersion.objects.create(
            organization=org,
            project=project,
            report=report,
            version_number=1,
            change_summary="Initial report draft initialized.",
            created_by=request.user,
        )

        raw_sections = body.get("sections") or [
            {"sectionKey": "executive_summary", "title": "1. Executive Summary", "content": report.executive_summary},
            {"sectionKey": "work_packages", "title": "2. Physical Work Packages & Progress", "content": ""},
            {"sectionKey": "qa_qc", "title": "3. QA/QC & Materials Testing Verification", "content": ""},
            {"sectionKey": "hse_governance", "title": "4. HSE & Regulatory Governance", "content": ""},
        ]
        for idx, sec in enumerate(raw_sections):
            ReportSection.objects.create(
                organization=org,
                project=project,
                report=report,
                report_version=version,
                section_key=sec.get("sectionKey") or sec.get("id") or f"sec_{idx + 1}",
                title=sec.get("title") or f"Section {idx + 1}",
                order_index=idx + 1,
                content_markdown=sec.get("content") or "",
            )

        record_audit_event(
            action="reports.created",
            target_type="Report",
            target_id=str(report.id),
            actor=request.user,
            organization=org,
            project=project,
            metadata={"reference_code": report.reference_code},
        )

        return api_success(
            data=serialize_report(report),
            message="Report draft created.",
            status_code=status.HTTP_201_CREATED,
        )


class ReportDetailView(APIView):
    """GET, PATCH /api/v1/reports/<uuid:report_id>/"""

    permission_classes = [IsTenantMember]

    def _get_report(self, request, report_id) -> Report:
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        return get_object_or_404(
            Report.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ),
            id=report_id,
        )

    @extend_schema(tags=["Reports"], summary="Get report details by ID")
    def get(self, request, report_id):
        report = self._get_report(request, report_id)
        if not request.user.is_platform_admin:
            can_draft_or_review = user_has_project_permission(
                request.user,
                report.organization_id,
                report.project_id,
                "reports.draft",
            ) or user_has_project_permission(
                request.user,
                report.organization_id,
                report.project_id,
                "reports.review",
            )
            if not can_draft_or_review and report.status not in (
                ReportStatus.APPROVED,
                ReportStatus.PUBLISHED,
            ):
                record_audit_event(
                    action="rbac.permission_denied",
                    target_type="Report",
                    target_id=str(report.id),
                    actor=request.user,
                    organization=report.organization,
                    project=report.project,
                    metadata={"reason": "stakeholder_unreleased_report_access", "status": report.status},
                )
                raise PermissionDenied("Read-only stakeholders can only access approved or released reports.")
        return api_success(data=serialize_report(report))

    @extend_schema(tags=["Reports"], summary="Update report sections, version, or workflow status")
    def patch(self, request, report_id):
        report = self._get_report(request, report_id)
        body = request.data or {}

        new_status = body.get("workflowStatus") or body.get("status")
        status_map = {
            "draft": ReportStatus.DRAFT,
            "review": ReportStatus.IN_REVIEW,
            "in_review": ReportStatus.IN_REVIEW,
            "changes_requested": ReportStatus.CHANGES_REQUESTED,
            "approved": ReportStatus.APPROVED,
            "released": ReportStatus.PUBLISHED,
            "published": ReportStatus.PUBLISHED,
        }
        target_status = status_map.get(new_status)

        if target_status in (ReportStatus.APPROVED, ReportStatus.PUBLISHED):
            required_cap = (
                "reports.release"
                if target_status == ReportStatus.PUBLISHED
                else "reports.approve"
            )
            enforce_report_approval_separation_of_duties(
                user=request.user,
                report=report,
                required_capability=required_cap,
            )
        elif not request.user.is_platform_admin:
            can_edit = user_has_project_permission(
                request.user,
                report.organization_id,
                report.project_id,
                "reports.draft",
            ) or user_has_project_permission(
                request.user,
                report.organization_id,
                report.project_id,
                "reports.review",
            )
            if not can_edit:
                enforce_project_permission(
                    user=request.user,
                    organization=report.organization,
                    project=report.project,
                    permission_codename="reports.draft",
                    target_type="Report",
                    target_id=str(report.id),
                )

        if "title" in body:
            report.title = body["title"]
        if "executiveSummary" in body:
            report.executive_summary = body["executiveSummary"]

        if target_status:
            report.status = target_status
            if report.status in (ReportStatus.APPROVED, ReportStatus.PUBLISHED):
                report.approved_by = request.user
            if report.status == ReportStatus.PUBLISHED and not report.published_at:
                report.published_at = timezone.now()
                report.locked_at = timezone.now()

        if isinstance(body.get("sections"), list):
            for idx, sec_data in enumerate(body["sections"]):
                sec_id = sec_data.get("id")
                sec_obj = report.sections.filter(id=sec_id).first() if sec_id and len(str(sec_id)) == 36 else None
                if sec_obj:
                    sec_obj.title = sec_data.get("title", sec_obj.title)
                    sec_obj.content_markdown = sec_data.get("content", sec_obj.content_markdown)
                    sec_obj.save()
                else:
                    ReportSection.objects.create(
                        organization=report.organization,
                        project=report.project,
                        report=report,
                        section_key=sec_data.get("sectionKey") or f"sec_{idx + 1}",
                        title=sec_data.get("title") or f"Section {idx + 1}",
                        order_index=idx + 1,
                        content_markdown=sec_data.get("content") or "",
                    )

        if body.get("createNewVersion"):
            report.current_version_number += 1
            ReportVersion.objects.create(
                organization=report.organization,
                project=report.project,
                report=report,
                version_number=report.current_version_number,
                change_summary=body.get("changeSummary") or f"Version {report.current_version_number}.0 snapshot",
                is_immutable=report.status == ReportStatus.PUBLISHED,
                created_by=request.user,
            )

        report.save()

        record_audit_event(
            action="reports.updated",
            target_type="Report",
            target_id=str(report.id),
            actor=request.user,
            organization=report.organization,
            project=report.project,
            metadata={"reference_code": report.reference_code, "status": report.status},
        )
        return api_success(data=serialize_report(report), message="Report updated.")


class ReportTemplateListCreateView(APIView):
    """GET, POST /api/v1/reports/templates/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Reports"], summary="List organization report templates")
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            qs = ReportTemplate.objects.all()
        else:
            qs = ReportTemplate.objects.filter(organization=request.organization)
        return api_success(data=[serialize_template(t) for t in qs])

    @extend_schema(tags=["Reports"], summary="Create an organization report template")
    def post(self, request):
        body = request.data or {}
        org = request.organization
        code = (body.get("code") or f"TPL-{int(timezone.now().timestamp()) % 10000}").upper()
        tpl = ReportTemplate.objects.create(
            organization=org,
            name=body.get("name") or "Standard Engineering Progress Template",
            code=code,
            report_type=body.get("report_type") or ReportType.WEEKLY_PROGRESS,
            description=body.get("description") or "",
            default_sections=body.get("defaultSections") or [],
            created_by=request.user,
        )
        return api_success(
            data=serialize_template(tpl),
            message="Report template created.",
            status_code=status.HTTP_201_CREATED,
        )
