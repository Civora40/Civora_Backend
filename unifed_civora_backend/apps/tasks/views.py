"""HTTP views for Tasks & Project Activities (Phase 2 & 6)."""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from apps.access_control.models import SystemRoleSlug
from apps.access_control.permissions import IsTenantMember
from apps.audit.services import record_audit_event
from apps.projects.models import ActivityStatus, ProjectActivity
from apps.projects.selectors import get_authorized_projects_for_user
from apps.users.models import User
from common.responses import api_success
from .models import Task, TaskAssignment, TaskPriority, TaskStatus


def serialize_task(task: Task) -> dict:
    first_assignment = task.assignments.select_related("assignee").first()
    assignee_user = first_assignment.assignee if first_assignment else task.created_by
    status_map = {
        TaskStatus.PENDING: "assigned",
        TaskStatus.IN_PROGRESS: "in_progress",
        TaskStatus.SUBMITTED_FOR_VERIFICATION: "submitted_for_review",
        TaskStatus.COMPLETED: "completed",
        TaskStatus.OVERDUE: "in_progress",
        TaskStatus.BLOCKED: "blocked",
        TaskStatus.CANCELLED: "cancelled",
    }
    fe_status = status_map.get(task.status, "assigned")
    linked_subs = [
        str(ev.submission.reference_code if ev.submission else ev.id)
        for ev in task.completion_evidence.select_related("submission").all()
    ]

    return {
        "id": str(task.id),
        "code": task.code,
        "title": task.title,
        "description": task.description,
        "projectId": str(task.project_id),
        "projectName": task.project.name if task.project else "",
        "assignedPersonnelId": str(assignee_user.id) if assignee_user else "",
        "assignedPersonnelName": assignee_user.full_name if assignee_user else "Assigned Engineer",
        "assignedPersonnelRole": (
            assignee_user.job_title if assignee_user and assignee_user.job_title else "Project Engineer"
        ),
        "category": task.department.name if task.department else "Site Work / Daily Progress",
        "status": fe_status,
        "rawStatus": task.status,
        "priority": task.priority,
        "dueDate": task.due_date.strftime("%Y-%m-%d") if task.due_date else timezone.now().strftime("%Y-%m-%d"),
        "linkedSubmissions": linked_subs,
        "acceptedBy": task.verified_by.full_name if task.verified_by else None,
        "acceptedAt": task.completed_at.isoformat() if task.completed_at else None,
    }


def serialize_activity(act: ProjectActivity) -> dict:
    lead = act.lead_engineer
    status_map = {
        ActivityStatus.PLANNED: "logged",
        ActivityStatus.IN_PROGRESS: "logged",
        ActivityStatus.COMPLETED: "verified",
        ActivityStatus.DELAYED: "disputed",
        ActivityStatus.BLOCKED: "disputed",
    }
    sub_ids = [str(s.reference_code) for s in act.submissions.all()]
    return {
        "id": str(act.id),
        "code": act.code,
        "projectId": str(act.project_id),
        "projectName": act.project.name if act.project else "",
        "personnelId": str(lead.id) if lead else "",
        "personnelName": lead.full_name if lead else (act.project.lead_engineer_name if act.project else "Site Lead"),
        "date": (
            act.actual_start_date.isoformat()
            if act.actual_start_date
            else act.created_at.strftime("%Y-%m-%d")
        ),
        "taskReference": act.wbs_reference or act.code,
        "taskTitle": act.name,
        "activityDescription": act.description or act.name,
        "submissionIds": sub_ids,
        "status": status_map.get(act.status, "logged"),
        "rawStatus": act.status,
        "progressPercent": act.progress_percent,
    }


class TaskListCreateView(APIView):
    """GET, POST /api/v1/tasks/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Tasks"], summary="List authorized project tasks and WBS activities")
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            task_qs = Task.objects.select_related(
                "project", "department", "created_by", "verified_by"
            ).prefetch_related("assignments__assignee", "completion_evidence__submission")
            act_qs = ProjectActivity.objects.select_related(
                "project", "department", "lead_engineer"
            ).prefetch_related("submissions")
        else:
            authorized_projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            )
            task_qs = Task.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ).select_related(
                "project", "department", "created_by", "verified_by"
            ).prefetch_related("assignments__assignee", "completion_evidence__submission")
            act_qs = ProjectActivity.objects.filter(
                organization=request.organization,
                project__in=authorized_projects,
            ).select_related("project", "department", "lead_engineer").prefetch_related("submissions")

        project_id = request.query_params.get("projectId")
        if project_id and project_id != "all":
            task_qs = task_qs.filter(project_id=project_id)
            act_qs = act_qs.filter(project_id=project_id)

        return api_success(
            data={
                "tasks": [serialize_task(t) for t in task_qs],
                "activities": [serialize_activity(a) for a in act_qs],
            }
        )

    @extend_schema(tags=["Tasks"], summary="Create a project task or WBS activity")
    def post(self, request):
        if not request.user.is_platform_admin:
            membership = getattr(request, "organization_membership", None)
            if membership and membership.role.slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER:
                raise PermissionDenied("Read-only stakeholders cannot create tasks or activities.")

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
            raise ValidationError({"projectId": ["A valid authorized project is required."]})

        if body.get("recordType") == "activity":
            code = body.get("code") or f"ACT-{project.code}-{int(timezone.now().timestamp()) % 10000}"
            act = ProjectActivity.objects.create(
                organization=org,
                project=project,
                code=code,
                name=body.get("title") or body.get("name") or "Site Construction Activity",
                description=body.get("activityDescription") or body.get("description") or "",
                wbs_reference=body.get("taskReference") or code,
                location_zone=body.get("locationZone") or project.location,
                status=ActivityStatus.IN_PROGRESS,
                lead_engineer=request.user,
            )
            return api_success(
                data=serialize_activity(act),
                message="Project activity logged.",
                status_code=status.HTTP_201_CREATED,
            )

        code = body.get("code") or f"TSK-{project.code}-{int(timezone.now().timestamp()) % 10000}"
        task = Task.objects.create(
            organization=org,
            project=project,
            code=code,
            title=body.get("title") or "Engineering Verification Task",
            description=body.get("description") or "",
            status=TaskStatus.PENDING,
            priority=body.get("priority") or TaskPriority.MEDIUM,
            due_date=timezone.now() + timezone.timedelta(days=3),
            created_by=request.user,
        )

        assignee_id = body.get("assignedPersonnelId")
        assignee = User.objects.filter(id=assignee_id).first() if assignee_id else request.user
        if assignee:
            TaskAssignment.objects.get_or_create(
                organization=org,
                project=project,
                task=task,
                assignee=assignee,
                defaults={"assigned_by": request.user},
            )

        record_audit_event(
            action="tasks.created",
            target_type="Task",
            target_id=str(task.id),
            actor=request.user,
            organization=org,
            project=project,
            metadata={"code": task.code, "title": task.title},
        )

        return api_success(
            data=serialize_task(task),
            message="Task created successfully.",
            status_code=status.HTTP_201_CREATED,
        )


class TaskDetailView(APIView):
    """PATCH /api/v1/tasks/<uuid:task_id>/"""

    permission_classes = [IsTenantMember]

    @extend_schema(tags=["Tasks"], summary="Update task status or verification")
    def patch(self, request, task_id):
        authorized_projects = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        task = get_object_or_404(
            Task.objects.filter(organization=request.organization, project__in=authorized_projects),
            id=task_id,
        )
        body = request.data or {}
        new_status = body.get("status")
        status_map = {
            "assigned": TaskStatus.PENDING,
            "pending": TaskStatus.PENDING,
            "in_progress": TaskStatus.IN_PROGRESS,
            "submitted_for_review": TaskStatus.SUBMITTED_FOR_VERIFICATION,
            "submitted_for_verification": TaskStatus.SUBMITTED_FOR_VERIFICATION,
            "completed": TaskStatus.COMPLETED,
            "blocked": TaskStatus.BLOCKED,
            "cancelled": TaskStatus.CANCELLED,
        }
        if new_status in status_map:
            task.status = status_map[new_status]
            if task.status == TaskStatus.COMPLETED:
                task.completed_at = timezone.now()
                task.verified_by = request.user
        if "description" in body:
            task.description = body["description"]
        task.save()

        record_audit_event(
            action="tasks.updated",
            target_type="Task",
            target_id=str(task.id),
            actor=request.user,
            organization=task.organization,
            project=task.project,
            metadata={"code": task.code, "status": task.status},
        )
        return api_success(data=serialize_task(task), message="Task updated.")
