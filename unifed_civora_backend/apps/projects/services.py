"""Write services for Projects domain (Phase 4)."""
from __future__ import annotations
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.audit.services import record_audit_event
from apps.organizations.models import OrganizationMembership
from .models import (
    ALLOWED_PROJECT_TRANSITIONS,
    ActivityStatus,
    Project,
    ProjectActivity,
    ProjectDepartment,
    ProjectMembership,
    ProjectStatus,
)


@transaction.atomic
def create_project(
    *,
    organization,
    name: str,
    code: str,
    location: str = "",
    description: str = "",
    status: str = ProjectStatus.DRAFT,
    created_by=None,
    client_name: str = "",
    lead_engineer_name: str = "",
    budget_formatted: str = "",
    specs_reference: str = "",
    project_metadata: dict = None,
    start_date=None,
    target_completion_date=None,
) -> Project:
    return Project.objects.create(
        organization=organization,
        name=name,
        code=code.upper(),
        location=location,
        description=description,
        status=status,
        created_by=created_by,
        client_name=client_name,
        lead_engineer_name=lead_engineer_name,
        budget_formatted=budget_formatted,
        specs_reference=specs_reference,
        project_metadata=project_metadata or {},
        start_date=start_date,
        target_completion_date=target_completion_date,
    )


@transaction.atomic
def transition_project_status(
    *,
    project: Project,
    new_status: str,
    actor=None,
    reason: str = "",
) -> Project:
    if not project.can_transition_to(new_status):
        allowed = ", ".join(sorted(ALLOWED_PROJECT_TRANSITIONS.get(project.status, set()))) or "None (terminal state)"
        raise ValidationError(
            f"Invalid project lifecycle transition from '{project.status}' to '{new_status}'. Allowed transitions: {allowed}."
        )
    old_status = project.status
    project.status = new_status
    project.save(update_fields=["status", "updated_at"])

    if actor:
        record_audit_event(
            action="projects.status_transitioned",
            target_type="Project",
            target_id=str(project.id),
            actor=actor,
            organization=project.organization,
            project=project,
            metadata={
                "previous_status": old_status,
                "new_status": new_status,
                "reason": reason,
                "project_code": project.code,
            },
        )
    return project


@transaction.atomic
def create_project_department(
    *,
    project: Project,
    name: str,
    code: str,
    description: str = "",
    actor=None,
) -> ProjectDepartment:
    code_clean = code.strip().upper()
    if ProjectDepartment.objects.filter(project=project, code=code_clean).exists():
        raise ValidationError(f"Department code '{code_clean}' already exists in this project.")

    department = ProjectDepartment.objects.create(
        organization=project.organization,
        project=project,
        name=name.strip(),
        code=code_clean,
        description=description.strip(),
    )
    if actor:
        record_audit_event(
            action="projects.department_created",
            target_type="ProjectDepartment",
            target_id=str(department.id),
            actor=actor,
            organization=project.organization,
            project=project,
            metadata={"code": department.code, "name": department.name},
        )
    return department


@transaction.atomic
def update_project_department(
    *,
    department: ProjectDepartment,
    name: str = None,
    code: str = None,
    description: str = None,
    actor=None,
) -> ProjectDepartment:
    updates = {}
    if name is not None:
        department.name = name.strip()
        updates["name"] = department.name
    if code is not None:
        code_clean = code.strip().upper()
        if (
            code_clean != department.code
            and ProjectDepartment.objects.filter(project=department.project, code=code_clean).exists()
        ):
            raise ValidationError(f"Department code '{code_clean}' already exists in this project.")
        department.code = code_clean
        updates["code"] = department.code
    if description is not None:
        department.description = description.strip()
        updates["description"] = department.description
    department.save()

    if actor:
        record_audit_event(
            action="projects.department_updated",
            target_type="ProjectDepartment",
            target_id=str(department.id),
            actor=actor,
            organization=department.organization,
            project=department.project,
            metadata=updates,
        )
    return department


@transaction.atomic
def delete_project_department(*, department: ProjectDepartment, actor=None) -> None:
    dept_id = str(department.id)
    dept_code = department.code
    dept_name = department.name
    proj = department.project
    org = department.organization

    department.delete()

    if actor:
        record_audit_event(
            action="projects.department_deleted",
            target_type="ProjectDepartment",
            target_id=dept_id,
            actor=actor,
            organization=org,
            project=proj,
            metadata={"code": dept_code, "name": dept_name},
        )


@transaction.atomic
def create_project_activity(
    *,
    project: Project,
    code: str,
    name: str,
    department: ProjectDepartment = None,
    description: str = "",
    wbs_reference: str = "",
    location_zone: str = "",
    status: str = ActivityStatus.PLANNED,
    progress_percent: int = 0,
    planned_start_date=None,
    planned_end_date=None,
    actual_start_date=None,
    actual_end_date=None,
    lead_engineer=None,
    actor=None,
) -> ProjectActivity:
    code_clean = code.strip().upper()
    if ProjectActivity.objects.filter(project=project, code=code_clean).exists():
        raise ValidationError(f"Activity code '{code_clean}' already exists in this project.")

    activity = ProjectActivity.objects.create(
        organization=project.organization,
        project=project,
        department=department,
        code=code_clean,
        name=name.strip(),
        description=description.strip(),
        wbs_reference=wbs_reference.strip(),
        location_zone=location_zone.strip(),
        status=status,
        progress_percent=progress_percent,
        planned_start_date=planned_start_date,
        planned_end_date=planned_end_date,
        actual_start_date=actual_start_date,
        actual_end_date=actual_end_date,
        lead_engineer=lead_engineer,
    )

    if actor:
        record_audit_event(
            action="projects.activity_created",
            target_type="ProjectActivity",
            target_id=str(activity.id),
            actor=actor,
            organization=project.organization,
            project=project,
            metadata={"code": activity.code, "name": activity.name, "status": activity.status},
        )
    return activity


@transaction.atomic
def update_project_activity(
    *,
    activity: ProjectActivity,
    actor=None,
    **updates,
) -> ProjectActivity:
    allowed_fields = (
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
        "department",
    )
    recorded_updates = {}
    for field, val in updates.items():
        if field in allowed_fields:
            setattr(activity, field, val)
            recorded_updates[field] = str(val.id) if hasattr(val, "id") else str(val)
    activity.save()

    if actor:
        record_audit_event(
            action="projects.activity_updated",
            target_type="ProjectActivity",
            target_id=str(activity.id),
            actor=actor,
            organization=activity.organization,
            project=activity.project,
            metadata=recorded_updates,
        )
    return activity


@transaction.atomic
def delete_project_activity(*, activity: ProjectActivity, actor=None) -> None:
    act_id = str(activity.id)
    act_code = activity.code
    act_name = activity.name
    proj = activity.project
    org = activity.organization

    activity.delete()

    if actor:
        record_audit_event(
            action="projects.activity_deleted",
            target_type="ProjectActivity",
            target_id=act_id,
            actor=actor,
            organization=org,
            project=proj,
            metadata={"code": act_code, "name": act_name},
        )


@transaction.atomic
def assign_user_to_project(
    *,
    project: Project,
    user,
    role,
    department: ProjectDepartment = None,
) -> ProjectMembership:
    # Enforce that user belongs to the project's owning organization
    is_org_member = OrganizationMembership.objects.filter(
        organization_id=project.organization_id,
        user=user,
        is_active=True,
    ).exists()
    if not is_org_member:
        raise ValidationError(
            "Cannot assign user to project: user is not an active member of the owning organization."
        )

    membership, _ = ProjectMembership.objects.update_or_create(
        organization=project.organization,
        project=project,
        user=user,
        defaults={
            "role": role,
            "department": department,
            "is_active": True,
        },
    )
    return membership
