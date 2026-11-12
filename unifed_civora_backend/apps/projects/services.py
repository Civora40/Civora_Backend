"""Write services for Projects domain (Phase 4 & Project Governance Gate)."""
from __future__ import annotations
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.audit.services import record_audit_event
from apps.notifications.models import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDeliveryLog,
    NotificationSeverity,
)
from apps.organizations.models import OrganizationMembership
from apps.users.models import User
from common.exceptions import ProjectApprovalRequiredError
from .models import (
    ALLOWED_PROJECT_TRANSITIONS,
    ActivityStatus,
    Project,
    ProjectActivity,
    ProjectApprovalStatus,
    ProjectDepartment,
    ProjectMembership,
    ProjectStatus,
)


def _dispatch_notification(
    *,
    organization,
    project: Project,
    recipient: User,
    actor: User | None,
    event_type: str,
    title: str,
    body: str,
    severity: str = NotificationSeverity.INFO,
    action_url: str = "/dashboard",
) -> None:
    if not recipient:
        return
    notif = Notification.objects.create(
        organization=organization,
        project=project,
        recipient=recipient,
        actor=actor,
        event_type=event_type,
        severity=severity,
        title=title,
        body=body,
        action_url=action_url,
    )
    NotificationDeliveryLog.objects.create(
        organization=organization,
        notification=notif,
        channel=NotificationChannel.IN_APP,
        recipient_address=recipient.email,
        status=DeliveryStatus.DELIVERED,
        delivered_at=timezone.now(),
    )


def notify_admins_project_pending_approval(*, project: Project, actor: User | None = None) -> None:
    """Notify all active Platform Administrators that a project awaits review & approval."""
    admins = User.objects.filter(is_platform_admin=True, is_active=True)
    creator_name = actor.full_name if actor else (project.created_by.full_name if project.created_by else "Project Engineer")
    org_name = project.organization.name if project.organization else "Organization"
    for admin_user in admins:
        _dispatch_notification(
            organization=project.organization,
            project=project,
            recipient=admin_user,
            actor=actor,
            event_type="project.approval_required",
            severity=NotificationSeverity.WARNING,
            title=f"Project Approval Required: {project.code}",
            body=(
                f"{creator_name} submitted '{project.name}' ({project.code}) under {org_name} "
                "for administrative review before field submissions can begin."
            ),
            action_url="/admin/projects",
        )


def _notify_project_engineers_review_outcome(
    *,
    project: Project,
    admin_user: User,
    event_type: str,
    title: str,
    body: str,
    severity: str,
) -> None:
    recipients = set()
    if project.created_by:
        recipients.add(project.created_by)
    for member in project.projects_projectmembership_records.filter(is_active=True).select_related("user", "role"):
        if member.role and member.role.slug in ("project_engineer", "supervising_reviewer"):
            recipients.add(member.user)
    for recipient in recipients:
        _dispatch_notification(
            organization=project.organization,
            project=project,
            recipient=recipient,
            actor=admin_user,
            event_type=event_type,
            severity=severity,
            title=title,
            body=body,
            action_url=f"/dashboard/projects/{project.id}",
        )


def ensure_project_allows_field_submissions(project: Project) -> None:
    """
    Enforces that no field submission can be created unless the Project has been
    reviewed and approved by a Platform Administrator and is in 'active' status.
    """
    block_reason = project.field_submission_block_reason()
    if block_reason:
        raise ProjectApprovalRequiredError(detail=block_reason)


@transaction.atomic
def create_project(
    *,
    organization,
    name: str,
    code: str,
    location: str = "",
    description: str = "",
    status: str = ProjectStatus.DRAFT,
    approval_status: str | None = None,
    reviewed_by=None,
    reviewed_at=None,
    review_notes: str = "",
    created_by=None,
    client_name: str = "",
    lead_engineer_name: str = "",
    lasbca_permit_number: str = "",
    budget_formatted: str = "",
    specs_reference: str = "",
    project_metadata: dict = None,
    start_date=None,
    target_completion_date=None,
) -> Project:
    if approval_status is None:
        if created_by and getattr(created_by, "is_platform_admin", False):
            approval_status = ProjectApprovalStatus.APPROVED
            reviewed_by = reviewed_by or created_by
            reviewed_at = reviewed_at or timezone.now()
        else:
            approval_status = ProjectApprovalStatus.PENDING_REVIEW

    return Project.objects.create(
        organization=organization,
        name=name,
        code=code.upper(),
        location=location,
        description=description,
        status=status,
        approval_status=approval_status,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        review_notes=review_notes,
        created_by=created_by,
        client_name=client_name,
        lead_engineer_name=lead_engineer_name,
        lasbca_permit_number=lasbca_permit_number,
        budget_formatted=budget_formatted,
        specs_reference=specs_reference,
        project_metadata=project_metadata or {},
        start_date=start_date,
        target_completion_date=target_completion_date,
    )


@transaction.atomic
def submit_project_for_approval(
    *,
    project: Project,
    actor: User,
    notes: str = "",
) -> Project:
    """Submits an engineer-created project to Platform Administrators for review."""
    if project.approval_status == ProjectApprovalStatus.APPROVED and project.status == ProjectStatus.ACTIVE:
        return project

    prev_status = project.status
    prev_approval = project.approval_status
    project.status = ProjectStatus.PENDING_APPROVAL
    project.approval_status = ProjectApprovalStatus.PENDING_REVIEW
    project.save(update_fields=["status", "approval_status", "updated_at"])

    record_audit_event(
        action="projects.submitted_for_approval",
        target_type="Project",
        target_id=str(project.id),
        actor=actor,
        organization=project.organization,
        project=project,
        metadata={
            "previous_status": prev_status,
            "previous_approval_status": prev_approval,
            "new_status": project.status,
            "notes": notes.strip(),
            "project_code": project.code,
        },
    )
    notify_admins_project_pending_approval(project=project, actor=actor)
    return project


@transaction.atomic
def review_project(
    *,
    project: Project,
    admin_user: User,
    decision: str,
    notes: str = "",
    target_status: str | None = None,
) -> Project:
    """
    Platform Administrator review decision on an engineer-created project.
    Only approved projects may transition to active status and receive field submissions.
    """
    if not admin_user or not getattr(admin_user, "is_platform_admin", False):
        raise PermissionDenied(
            "Only a Platform Administrator can review and approve projects for field submissions."
        )

    normalized_decision = (decision or "").strip().lower()
    if normalized_decision not in (
        "approve",
        "approved",
        "reject",
        "rejected",
        "request_changes",
        "changes_requested",
    ):
        raise ValidationError(
            "Invalid review decision. Must be 'approve', 'request_changes', or 'reject'."
        )

    clean_notes = (notes or "").strip()
    prev_status = project.status
    prev_approval = project.approval_status
    now = timezone.now()

    if normalized_decision in ("approve", "approved"):
        next_status = (
            target_status
            if target_status in (ProjectStatus.SETUP, ProjectStatus.ACTIVE)
            else ProjectStatus.ACTIVE
        )
        project.approval_status = ProjectApprovalStatus.APPROVED
        project.status = next_status
        project.reviewed_by = admin_user
        project.reviewed_at = now
        project.review_notes = clean_notes or "Approved by Platform Administrator for field operations."
        project.save(
            update_fields=[
                "approval_status",
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_notes",
                "updated_at",
            ]
        )
        record_audit_event(
            action="projects.approved",
            target_type="Project",
            target_id=str(project.id),
            actor=admin_user,
            organization=project.organization,
            project=project,
            metadata={
                "previous_status": prev_status,
                "previous_approval_status": prev_approval,
                "new_status": project.status,
                "review_notes": project.review_notes,
                "project_code": project.code,
            },
        )
        _notify_project_engineers_review_outcome(
            project=project,
            admin_user=admin_user,
            event_type="project.approved",
            title=f"Project {project.code} Approved for Field Submissions",
            body=(
                f"{admin_user.full_name} approved '{project.name}' ({project.code}). "
                f"Status is now '{project.status}' and field submissions are unlocked."
            ),
            severity=NotificationSeverity.INFO,
        )
        return project

    if not clean_notes:
        raise ValidationError("Review notes/reason are required when rejecting or requesting changes on a project.")

    if normalized_decision in ("request_changes", "changes_requested"):
        project.approval_status = ProjectApprovalStatus.CHANGES_REQUESTED
        project.status = ProjectStatus.DRAFT
        audit_action = "projects.changes_requested"
        notif_event = "project.changes_requested"
        notif_title = f"Changes Requested for Project {project.code}"
        notif_body = (
            f"{admin_user.full_name} requested changes on '{project.name}' ({project.code}) "
            f"before field submissions can be authorized: {clean_notes}"
        )
    else:
        project.approval_status = ProjectApprovalStatus.REJECTED
        project.status = ProjectStatus.REJECTED
        audit_action = "projects.rejected"
        notif_event = "project.rejected"
        notif_title = f"Project {project.code} Rejected During Admin Review"
        notif_body = (
            f"{admin_user.full_name} rejected '{project.name}' ({project.code}): {clean_notes}"
        )

    project.reviewed_by = admin_user
    project.reviewed_at = now
    project.review_notes = clean_notes
    project.save(
        update_fields=[
            "approval_status",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_notes",
            "updated_at",
        ]
    )
    record_audit_event(
        action=audit_action,
        target_type="Project",
        target_id=str(project.id),
        actor=admin_user,
        organization=project.organization,
        project=project,
        metadata={
            "previous_status": prev_status,
            "previous_approval_status": prev_approval,
            "new_status": project.status,
            "review_notes": clean_notes,
            "project_code": project.code,
        },
    )
    _notify_project_engineers_review_outcome(
        project=project,
        admin_user=admin_user,
        event_type=notif_event,
        title=notif_title,
        body=notif_body,
        severity=NotificationSeverity.WARNING,
    )
    return project


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

    is_admin = bool(actor and getattr(actor, "is_platform_admin", False))
    if new_status == ProjectStatus.ACTIVE and project.approval_status != ProjectApprovalStatus.APPROVED:
        if not is_admin:
            raise ValidationError(
                "Project must be reviewed and approved by a Platform Administrator before transitioning to 'active' or accepting field submissions."
            )
        project.approval_status = ProjectApprovalStatus.APPROVED
        project.reviewed_by = actor
        project.reviewed_at = timezone.now()
        if reason and not project.review_notes:
            project.review_notes = reason.strip()

    old_status = project.status
    project.status = new_status
    if new_status == ProjectStatus.PENDING_APPROVAL:
        project.approval_status = ProjectApprovalStatus.PENDING_REVIEW

    project.save()

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
                "approval_status": project.approval_status,
                "reason": reason,
                "project_code": project.code,
            },
        )
        if new_status == ProjectStatus.PENDING_APPROVAL:
            notify_admins_project_pending_approval(project=project, actor=actor)

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
