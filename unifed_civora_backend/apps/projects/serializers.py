"""Serializers for Projects domain (Phase 4)."""
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers
from .models import ActivityStatus, Project, ProjectActivity, ProjectDepartment, ProjectMembership, ProjectStatus


def serialize_project_department(dept: ProjectDepartment) -> dict:
    return {
        "id": str(dept.id),
        "organization": str(dept.organization_id),
        "organizationId": str(dept.organization_id),
        "project": str(dept.project_id),
        "projectId": str(dept.project_id),
        "name": dept.name,
        "code": dept.code,
        "description": dept.description,
        "created_at": dept.created_at.isoformat() if dept.created_at else None,
        "createdAt": dept.created_at.isoformat() if dept.created_at else None,
        "updated_at": dept.updated_at.isoformat() if dept.updated_at else None,
        "updatedAt": dept.updated_at.isoformat() if dept.updated_at else None,
    }


def serialize_project_activity(activity: ProjectActivity) -> dict:
    dept = activity.department
    lead = activity.lead_engineer
    return {
        "id": str(activity.id),
        "organization": str(activity.organization_id),
        "organizationId": str(activity.organization_id),
        "project": str(activity.project_id),
        "projectId": str(activity.project_id),
        "code": activity.code,
        "name": activity.name,
        "description": activity.description,
        "wbs_reference": activity.wbs_reference,
        "wbsReference": activity.wbs_reference,
        "location_zone": activity.location_zone,
        "locationZone": activity.location_zone,
        "status": activity.status,
        "progress_percent": activity.progress_percent,
        "progressPercent": activity.progress_percent,
        "planned_start_date": activity.planned_start_date.isoformat() if activity.planned_start_date else None,
        "plannedStartDate": activity.planned_start_date.isoformat() if activity.planned_start_date else None,
        "planned_end_date": activity.planned_end_date.isoformat() if activity.planned_end_date else None,
        "plannedEndDate": activity.planned_end_date.isoformat() if activity.planned_end_date else None,
        "actual_start_date": activity.actual_start_date.isoformat() if activity.actual_start_date else None,
        "actualStartDate": activity.actual_start_date.isoformat() if activity.actual_start_date else None,
        "actual_end_date": activity.actual_end_date.isoformat() if activity.actual_end_date else None,
        "actualEndDate": activity.actual_end_date.isoformat() if activity.actual_end_date else None,
        "department_id": str(dept.id) if dept else None,
        "departmentId": str(dept.id) if dept else None,
        "department_code": dept.code if dept else None,
        "departmentCode": dept.code if dept else None,
        "department_name": dept.name if dept else None,
        "departmentName": dept.name if dept else None,
        "lead_engineer_id": str(lead.id) if lead else None,
        "leadEngineerId": str(lead.id) if lead else None,
        "lead_engineer_name": lead.full_name if lead else None,
        "leadEngineerName": lead.full_name if lead else None,
        "created_at": activity.created_at.isoformat() if activity.created_at else None,
        "createdAt": activity.created_at.isoformat() if activity.created_at else None,
        "updated_at": activity.updated_at.isoformat() if activity.updated_at else None,
        "updatedAt": activity.updated_at.isoformat() if activity.updated_at else None,
    }


def serialize_project(project: Project) -> dict:
    meta = project.project_metadata or {}
    org_name = project.organization.name if project.organization else ""
    client_str = project.client_name or meta.get("client") or org_name
    lead_eng = (
        project.lead_engineer_name
        or meta.get("leadEngineer")
        or (project.created_by.full_name if getattr(project, "created_by", None) else "")
        or "Assigned Project Engineer"
    )
    budget_fmt = project.budget_formatted or meta.get("budgetFormatted") or "₦0"
    specs_ref = (
        project.specs_reference
        or meta.get("specsReference")
        or "Eurocode 2 & FMWH Standard Specifications (C35/45)"
    )
    project_type = meta.get("projectType") or "Civil Infrastructure"
    scope_summary = meta.get("scopeSummary") or project.description or ""
    spec_folder = meta.get("specificationFolder") or {
        "id": f"fld_spec_{project.id}",
        "name": f"{project.name} — Specification Folder",
        "code": f"{project.code}-SPEC",
        "sector": project_type,
        "specsReference": specs_ref,
        "description": scope_summary or f"Project specification, deliverables, and QA governance folder for {project.name} ({project.code}).",
        "projectId": str(project.id),
    }

    fe_status = (
        "planning"
        if project.status in ("draft", "setup")
        else project.status
    )

    block_reason = project.field_submission_block_reason()
    reviewer = getattr(project, "reviewed_by", None)
    creator = getattr(project, "created_by", None)

    departments = [
        serialize_project_department(d)
        for d in project.projects_projectdepartment_records.all()
    ]

    active_personnel_count = project.projects_projectmembership_records.filter(is_active=True).count()
    db_submissions_count = project.submissions_submission_records.count()
    db_pending_reviews_count = project.submissions_submission_records.filter(
        status__in=["received", "pending_review", "needs_clarification"]
    ).count()
    db_reports_count = project.reports_report_records.count()
    db_activities_count = project.projects_projectactivity_records.count()
    total_submissions = db_submissions_count or meta.get("submissionsCount", 0)
    total_pending = db_pending_reviews_count or meta.get("pendingReviewsCount", 0)
    total_reports = db_reports_count or meta.get("reportsCount", 0)

    return {
        # Backend snake_case
        "id": str(project.id),
        "organization": str(project.organization_id),
        "name": project.name,
        "code": project.code,
        "client_name": client_str,
        "location": project.location,
        "description": project.description,
        "status": project.status,
        "approval_status": project.approval_status,
        "is_admin_approved": project.is_admin_approved,
        "can_accept_field_submissions": project.can_accept_field_submissions,
        "field_submission_block_reason": block_reason,
        "reviewed_by": str(reviewer.id) if reviewer else None,
        "reviewed_at": project.reviewed_at.isoformat() if project.reviewed_at else None,
        "review_notes": project.review_notes,
        "created_by": str(creator.id) if creator else None,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "target_completion_date": (
            project.target_completion_date.isoformat() if project.target_completion_date else None
        ),
        "departments": departments,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        # Frontend camelCase (Civora Frontend & Civora_Admin Project contracts)
        "organizationId": str(project.organization_id),
        "organizationName": org_name,
        "client": client_str,
        "clientName": client_str,
        "frontendStatus": fe_status,
        "approvalStatus": project.approval_status,
        "isAdminApproved": project.is_admin_approved,
        "canAcceptFieldSubmissions": project.can_accept_field_submissions,
        "fieldSubmissionBlockReason": block_reason,
        "reviewedById": str(reviewer.id) if reviewer else None,
        "reviewedByName": reviewer.full_name if reviewer else None,
        "reviewedByEmail": reviewer.email if reviewer else None,
        "reviewedAt": project.reviewed_at.isoformat() if project.reviewed_at else None,
        "reviewNotes": project.review_notes,
        "createdById": str(creator.id) if creator else None,
        "createdByName": creator.full_name if creator else None,
        "createdByEmail": creator.email if creator else None,
        "leadEngineer": lead_eng,
        "corenLeadEngineer": lead_eng,
        "lasbcaPermitNumber": project.lasbca_permit_number or meta.get("lasbcaPermitNumber") or "",
        "progressPercent": project.progress_percent,
        "submissionsCount": total_submissions,
        "activeSubmissionsCount": total_submissions,
        "activePersonnelCount": active_personnel_count or meta.get("activePersonnelCount", 0),
        "pendingReviewsCount": total_pending,
        "unresolvedFlagsCount": meta.get("unresolvedFlagsCount", 0),
        "reportsCount": total_reports,
        "activitiesCount": db_activities_count,
        "lastActivity": meta.get("lastActivity", "Just now"),
        "budgetFormatted": budget_fmt,
        "contractSum": budget_fmt,
        "specsReference": specs_ref,
        "specificationFolder": spec_folder,
        "startDate": project.start_date.isoformat() if project.start_date else meta.get("startDate", ""),
        "targetCompletionDate": (
            project.target_completion_date.isoformat()
            if project.target_completion_date
            else meta.get("completionDate", "")
        ),
        "completionDate": (
            project.target_completion_date.isoformat()
            if project.target_completion_date
            else meta.get("completionDate", "")
        ),
        "integrationsActive": meta.get("submissionChannels", ["whatsapp", "telegram"]),
        "createdAt": project.created_at.isoformat() if project.created_at else None,
        "updatedAt": project.updated_at.isoformat() if project.updated_at else None,
        **meta,
    }


class ProjectDepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectDepartment
        fields = ["id", "organization", "project", "name", "code", "description", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]

    def to_representation(self, instance):
        return serialize_project_department(instance)


class ProjectActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectActivity
        fields = [
            "id",
            "organization",
            "project",
            "department",
            "code",
            "name",
            "description",
            "wbs_reference",
            "location_zone",
            "status",
            "progress_percent",
            "planned_start_date",
            "planned_end_date",
            "actual_start_date",
            "actual_end_date",
            "lead_engineer",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def to_representation(self, instance):
        return serialize_project_activity(instance)


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            "id",
            "organization",
            "name",
            "code",
            "client_name",
            "location",
            "description",
            "status",
            "start_date",
            "target_completion_date",
            "created_at",
        ]
        read_only_fields = ["id", "organization", "created_at"]

    def to_representation(self, instance):
        return serialize_project(instance)


class ProjectMembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_full_name = serializers.CharField(source="user.full_name", read_only=True)
    role_slug = serializers.CharField(source="role.slug", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = ProjectMembership
        fields = [
            "id",
            "organization",
            "project",
            "user",
            "user_email",
            "user_full_name",
            "role",
            "role_slug",
            "department",
            "department_name",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "organization", "created_at"]
