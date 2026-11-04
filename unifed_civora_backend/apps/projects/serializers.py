"""Serializers for Projects domain (Phase 4 / Task 002)."""
from rest_framework import serializers
from .models import Project, ProjectDepartment, ProjectMembership


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

    # Map backend ProjectStatus (`draft`, `setup`, `active`, `on_hold`, `completed`, `archived`)
    # to frontend status expectations while preserving canonical `status`
    fe_status = (
        "planning"
        if project.status in ("draft", "setup")
        else project.status
    )

    departments = [
        {
            "id": str(d.id),
            "organization": str(d.organization_id),
            "project": str(d.project_id),
            "name": d.name,
            "code": d.code,
            "description": d.description,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in project.projects_projectdepartment_records.all()
    ]

    active_personnel_count = project.projects_projectmembership_records.filter(is_active=True).count()
    db_submissions_count = project.submissions_submission_records.count()
    db_pending_reviews_count = project.submissions_submission_records.filter(
        status__in=["received", "pending_review", "needs_clarification"]
    ).count()
    db_reports_count = project.reports_report_records.count()
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
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "organization", "created_at"]
