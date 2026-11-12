"""HTTP views for Projects domain (Phase 4 & Project Review/Approval Gate)."""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.permissions import HasOrganizationCapability, IsTenantMember
from apps.access_control.services import enforce_project_permission
from apps.audit.models import AuditLog
from apps.audit.services import record_audit_event
from apps.audit.views import serialize_audit_log
from apps.organizations.models import Organization
from apps.users.models import User
from common.responses import api_success
from .models import (
    ActivityStatus,
    Project,
    ProjectActivity,
    ProjectApprovalStatus,
    ProjectDepartment,
    ProjectMembership,
    ProjectStatus,
)
from .selectors import get_authorized_projects_for_user, get_project_members
from .serializers import (
    ProjectActivitySerializer,
    ProjectDepartmentSerializer,
    ProjectMembershipSerializer,
    ProjectSerializer,
    serialize_project,
    serialize_project_activity,
    serialize_project_department,
)
from .services import (
    assign_user_to_project,
    create_project,
    create_project_activity,
    create_project_department,
    delete_project_activity,
    delete_project_department,
    notify_admins_project_pending_approval,
    review_project,
    submit_project_for_approval,
    transition_project_status,
    update_project_activity,
    update_project_department,
)


def _get_authorized_project(request, project_id):
    if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
        qs = Project.objects.select_related("organization", "reviewed_by", "created_by").all()
    else:
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        ).select_related("organization", "reviewed_by", "created_by")
    return get_object_or_404(qs, id=project_id)

CreateProjectRequestSerializer = inline_serializer(
    name="CreateProjectRequest",
    fields={
        "name": serializers.CharField(help_text="Project name"),
        "code": serializers.CharField(help_text="Unique project code (e.g. LCR-S3-2026)"),
        "client": serializers.CharField(help_text="Sponsoring ministry, agency, or client organization"),
        "location": serializers.CharField(required=False, allow_blank=True),
        "description": serializers.CharField(required=False, allow_blank=True),
        "status": serializers.ChoiceField(
            choices=["draft", "pending_approval", "setup", "active", "planning"],
            required=False,
            default="pending_approval",
        ),
        "leadEngineer": serializers.CharField(required=False, allow_blank=True),
        "lasbcaPermitNumber": serializers.CharField(required=False, allow_blank=True),
        "budgetAmount": serializers.CharField(required=False, allow_blank=True),
        "currency": serializers.CharField(required=False, default="₦"),
        "specsReference": serializers.CharField(required=False, allow_blank=True),
        "projectType": serializers.CharField(required=False, allow_blank=True),
        "contractRef": serializers.CharField(required=False, allow_blank=True),
        "priority": serializers.ChoiceField(
            choices=["low", "normal", "high", "critical"], required=False, default="normal"
        ),
        "country": serializers.CharField(required=False, default="Nigeria"),
        "state": serializers.CharField(required=False, allow_blank=True),
        "city": serializers.CharField(required=False, allow_blank=True),
        "siteAddress": serializers.CharField(required=False, allow_blank=True),
        "coordinates": serializers.CharField(required=False, allow_blank=True),
        "startDate": serializers.CharField(required=False, allow_blank=True),
        "completionDate": serializers.CharField(required=False, allow_blank=True),
        "scopeSummary": serializers.CharField(required=False, allow_blank=True),
        "deliverables": serializers.ListField(child=serializers.CharField(), required=False),
        "milestonesList": serializers.ListField(child=serializers.DictField(), required=False),
        "contractor": serializers.CharField(required=False, allow_blank=True),
        "clientRep": serializers.CharField(required=False, allow_blank=True),
        "reportingCadence": serializers.CharField(required=False, default="daily"),
        "submissionChannels": serializers.ListField(child=serializers.CharField(), required=False),
    },
)

UpdateProjectRequestSerializer = inline_serializer(
    name="UpdateProjectRequest",
    fields={
        "name": serializers.CharField(required=False),
        "location": serializers.CharField(required=False),
        "description": serializers.CharField(required=False),
        "client_name": serializers.CharField(required=False),
        "lead_engineer_name": serializers.CharField(required=False),
        "budget_formatted": serializers.CharField(required=False),
        "specs_reference": serializers.CharField(required=False),
        "lasbca_permit_number": serializers.CharField(required=False),
        "status": serializers.ChoiceField(
            choices=[
                "draft",
                "pending_approval",
                "setup",
                "active",
                "on_hold",
                "completed",
                "rejected",
                "archived",
            ],
            required=False,
        ),
        "reason": serializers.CharField(required=False, allow_blank=True),
    },
)

TransitionStatusRequestSerializer = inline_serializer(
    name="TransitionProjectStatusRequest",
    fields={
        "status": serializers.ChoiceField(
            choices=[
                "draft",
                "pending_approval",
                "setup",
                "active",
                "on_hold",
                "completed",
                "rejected",
                "archived",
            ],
            help_text="Target lifecycle status",
        ),
        "reason": serializers.CharField(
            required=False,
            allow_blank=True,
            help_text="Reason for status transition",
        ),
    },
)

ReviewProjectRequestSerializer = inline_serializer(
    name="ReviewProjectRequest",
    fields={
        "decision": serializers.ChoiceField(
            choices=["approve", "request_changes", "reject"],
            help_text="Administrator decision on engineer-created project",
        ),
        "notes": serializers.CharField(
            required=False,
            allow_blank=True,
            help_text="Review feedback or justification (required when rejecting or requesting changes)",
        ),
        "targetStatus": serializers.ChoiceField(
            choices=["setup", "active"],
            required=False,
            help_text="Target status when approving (defaults to 'active')",
        ),
    },
)

SubmitProjectForApprovalRequestSerializer = inline_serializer(
    name="SubmitProjectForApprovalRequest",
    fields={
        "notes": serializers.CharField(
            required=False,
            allow_blank=True,
            help_text="Optional submission notes for the Platform Administrator",
        ),
    },
)

CreateDepartmentRequestSerializer = inline_serializer(
    name="CreateProjectDepartmentRequest",
    fields={
        "name": serializers.CharField(help_text="Department name (e.g. Civil / Structural)"),
        "code": serializers.CharField(help_text="Unique short code (e.g. CIV-STR)"),
        "description": serializers.CharField(required=False, allow_blank=True),
    },
)

CreateActivityRequestSerializer = inline_serializer(
    name="CreateProjectActivityRequest",
    fields={
        "code": serializers.CharField(help_text="WBS activity code (e.g. ACT-001)"),
        "name": serializers.CharField(help_text="Activity name"),
        "departmentId": serializers.UUIDField(required=False),
        "department_id": serializers.UUIDField(required=False),
        "description": serializers.CharField(required=False, allow_blank=True),
        "wbsReference": serializers.CharField(required=False, allow_blank=True),
        "wbs_reference": serializers.CharField(required=False, allow_blank=True),
        "locationZone": serializers.CharField(required=False, allow_blank=True),
        "location_zone": serializers.CharField(required=False, allow_blank=True),
        "status": serializers.ChoiceField(
            choices=["planned", "in_progress", "completed", "delayed", "blocked"],
            required=False,
            default="planned",
        ),
        "progressPercent": serializers.IntegerField(required=False, default=0),
        "progress_percent": serializers.IntegerField(required=False, default=0),
        "plannedStartDate": serializers.DateField(required=False),
        "planned_start_date": serializers.DateField(required=False),
        "plannedEndDate": serializers.DateField(required=False),
        "planned_end_date": serializers.DateField(required=False),
        "leadEngineerId": serializers.UUIDField(required=False),
        "lead_engineer_id": serializers.UUIDField(required=False),
    },
)


class ProjectListView(APIView):
    """
    GET, POST /api/v1/projects/
    Lists tenant projects authorized for the current user or creates a new project.
    """

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="List authorized infrastructure projects",
        description="Returns all projects for Platform Admins or tenant projects assigned to the authenticated user.",
        responses={200: ProjectSerializer(many=True)},
    )
    def get(self, request):
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            projects = Project.objects.select_related(
                "organization", "reviewed_by", "created_by"
            ).prefetch_related(
                "projects_projectdepartment_records",
                "projects_projectmembership_records",
                "projects_projectactivity_records",
            ).all()
        else:
            projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            ).select_related(
                "organization", "reviewed_by", "created_by"
            ).prefetch_related(
                "projects_projectdepartment_records",
                "projects_projectmembership_records",
                "projects_projectactivity_records",
            )

        status_filter = (request.query_params.get("status") or "").strip().lower()
        approval_filter = (
            request.query_params.get("approvalStatus")
            or request.query_params.get("approval_status")
            or ""
        ).strip().lower()
        if status_filter:
            projects = projects.filter(status=status_filter)
        if approval_filter:
            projects = projects.filter(approval_status=approval_filter)

        serializer = ProjectSerializer(projects, many=True)
        return api_success(data=serializer.data)

    @extend_schema(
        tags=["Projects"],
        summary="Create a new infrastructure project",
        description=(
            "Creates a new project under the active tenant organization. Projects created by engineers "
            "require Platform Administrator review and approval before transitioning to active or accepting "
            "field submissions."
        ),
        request=CreateProjectRequestSerializer,
        responses={201: ProjectSerializer},
    )
    def post(self, request):
        body = request.data or {}
        org = request.organization
        if request.user.is_platform_admin and (body.get("organizationId") or body.get("organization_id")):
            org = get_object_or_404(
                Organization, id=body.get("organizationId") or body.get("organization_id")
            )

        if not request.user.is_platform_admin:
            membership = getattr(request, "organization_membership", None)
            if not membership or membership.role.slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER:
                raise PermissionDenied("Forbidden: Insufficient permissions to create projects.")

        name = (body.get("name") or "").strip()
        code = (body.get("code") or "").strip().upper()
        client_name = (body.get("client") or body.get("client_name") or body.get("clientName") or "").strip()

        if not name:
            raise ValidationError({"name": ["Project name is required"]})
        if not code:
            raise ValidationError({"code": ["Project code is required"]})
        if not client_name:
            raise ValidationError({"client": ["Client or sponsoring organization is required"]})

        if Project.objects.filter(organization=org, code__iexact=code).exists():
            return api_success(
                data={"error": f"Project code '{code}' is already registered in the system"},
                message=f"Project code '{code}' is already registered in the system",
                status_code=status.HTTP_409_CONFLICT,
            )

        currency_symbol = body.get("currency") or "₦"
        raw_budget = str(body.get("budgetAmount") or body.get("budget_formatted") or "0").strip()
        if any(s in raw_budget for s in ("B", "M", "K", "₦")):
            formatted_budget = raw_budget if raw_budget.startswith(currency_symbol) else f"{currency_symbol}{raw_budget}"
        else:
            formatted_budget = f"{currency_symbol}{raw_budget}M"

        location_str = body.get("location") or ", ".join(
            [p for p in [body.get("city"), body.get("state"), body.get("country")] if p]
        ) or "Authorized Field Site"

        req_status = (body.get("status") or "").strip().lower()
        is_admin = bool(request.user.is_platform_admin)
        if is_admin:
            if req_status == "planning":
                db_status = ProjectStatus.SETUP
            elif req_status in dict(ProjectStatus.choices):
                db_status = req_status
            else:
                db_status = ProjectStatus.ACTIVE
            approval_status = ProjectApprovalStatus.APPROVED
            reviewed_by = request.user
            reviewed_at = timezone.now()
            review_notes = body.get("reviewNotes") or "Auto-approved upon creation by Platform Administrator."
        else:
            # Engineers cannot self-approve a project into 'active' operations; it enters Admin review or stays draft/setup.
            if req_status == ProjectStatus.DRAFT:
                db_status = ProjectStatus.DRAFT
            elif req_status in (ProjectStatus.SETUP, "planning"):
                db_status = ProjectStatus.SETUP
            else:
                db_status = ProjectStatus.PENDING_APPROVAL
            approval_status = ProjectApprovalStatus.PENDING_REVIEW
            reviewed_by = None
            reviewed_at = None
            review_notes = ""

        specs_reference = body.get("specsReference") or "Eurocode 2 & FMWH Standard Specifications (C35/45)"
        project_type = body.get("projectType") or "Road / Highway"
        scope_summary = body.get("scopeSummary") or body.get("description") or ""

        project = create_project(
            organization=org,
            name=name,
            code=code,
            client_name=client_name,
            location=location_str,
            description=body.get("description") or "",
            status=db_status,
            approval_status=approval_status,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            review_notes=review_notes,
            lead_engineer_name=body.get("leadEngineer") or request.user.full_name,
            lasbca_permit_number=body.get("lasbcaPermitNumber") or "",
            budget_formatted=formatted_budget,
            specs_reference=specs_reference,
            project_metadata={
                "projectType": project_type,
                "contractRef": body.get("contractRef") or "",
                "priority": body.get("priority") or "normal",
                "country": body.get("country") or "Nigeria",
                "state": body.get("state") or "",
                "city": body.get("city") or "",
                "siteAddress": body.get("siteAddress") or "",
                "coordinates": body.get("coordinates") or "",
                "startDate": body.get("startDate") or "",
                "completionDate": body.get("completionDate") or "",
                "scopeSummary": scope_summary,
                "deliverables": body.get("deliverables") if isinstance(body.get("deliverables"), list) else [],
                "milestonesList": body.get("milestonesList") if isinstance(body.get("milestonesList"), list) else [],
                "constraints": body.get("constraints") or "",
                "contractor": body.get("contractor") or "",
                "clientRep": body.get("clientRep") or "",
                "stakeholders": body.get("stakeholders") if isinstance(body.get("stakeholders"), list) else [],
                "assignedTeam": body.get("assignedTeam") if isinstance(body.get("assignedTeam"), list) else [],
                "escalationContact": body.get("escalationContact") or "",
                "reportingCadence": body.get("reportingCadence") or "daily",
                "submissionChannels": (
                    body.get("submissionChannels")
                    if isinstance(body.get("submissionChannels"), list)
                    else ["whatsapp", "telegram", "portal"]
                ),
                "reportingCategories": (
                    body.get("reportingCategories") if isinstance(body.get("reportingCategories"), list) else []
                ),
                "stakeholderReviewRequired": body.get("stakeholderReviewRequired", True),
                "requireLabBreakCert": body.get("requireLabBreakCert", True),
                "requireGeotaggedPhotos": body.get("requireGeotaggedPhotos", True),
                "governanceNotes": body.get("governanceNotes") or "",
                "specificationFolder": {
                    "code": f"{code}-SPEC",
                    "name": f"{name} — Specification Folder",
                    "sector": project_type,
                    "specsReference": specs_reference,
                    "description": scope_summary or f"Project specification, deliverables, and QA governance folder for {name} ({code}).",
                },
            },
            created_by=request.user,
        )

        pe_role = (
            getattr(request, "organization_membership", None).role
            if getattr(request, "organization_membership", None)
            else Role.objects.filter(slug=SystemRoleSlug.PROJECT_ENGINEER).first()
        )
        if pe_role:
            assign_user_to_project(project=project, user=request.user, role=pe_role)

        record_audit_event(
            action="projects.created",
            target_type="Project",
            target_id=str(project.id),
            actor=request.user,
            organization=org,
            project=project,
            metadata={
                "code": project.code,
                "name": project.name,
                "initial_status": project.status,
                "approval_status": project.approval_status,
            },
        )

        if project.status == ProjectStatus.PENDING_APPROVAL and not is_admin:
            notify_admins_project_pending_approval(project=project, actor=request.user)
            response_message = (
                "Project created and submitted to Platform Administrator for review and approval "
                "before field submissions can begin."
            )
        elif not is_admin:
            response_message = (
                "Project saved. Submit it for Platform Administrator review and approval before field submissions."
            )
        else:
            response_message = "Project created and approved for field submissions."

        return api_success(
            data=serialize_project(project),
            message=response_message,
            status_code=status.HTTP_201_CREATED,
        )


class ProjectDetailView(APIView):
    """GET, PATCH /api/v1/projects/<uuid:project_id>/"""

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="Get project details by ID",
        responses={200: ProjectSerializer},
    )
    def get(self, request, project_id):
        project = _get_authorized_project(request, project_id)
        return api_success(data=serialize_project(project))

    @extend_schema(
        tags=["Projects"],
        summary="Update project attributes or transition status",
        request=UpdateProjectRequestSerializer,
        responses={200: ProjectSerializer},
    )
    def patch(self, request, project_id):
        project = _get_authorized_project(request, project_id)
        enforce_project_permission(
            user=request.user,
            organization=project.organization,
            project=project,
            permission_codename="projects.update",
            target_type="Project",
            target_id=str(project.id),
        )
        payload = request.data or {}

        if "status" in payload and payload["status"] != project.status:
            try:
                transition_project_status(
                    project=project,
                    new_status=payload["status"],
                    actor=request.user,
                    reason=payload.get("reason", ""),
                )
            except DjangoValidationError as exc:
                raise ValidationError({"status": exc.messages})

        for field in (
            "name",
            "location",
            "description",
            "client_name",
            "lead_engineer_name",
            "budget_formatted",
            "specs_reference",
            "lasbca_permit_number",
        ):
            if field in payload:
                setattr(project, field, payload[field])
        project.save()

        record_audit_event(
            action="projects.updated",
            target_type="Project",
            target_id=str(project.id),
            actor=request.user,
            organization=project.organization,
            project=project,
            metadata={"updated_fields": list(payload.keys())},
        )
        return api_success(data=serialize_project(project), message="Project updated.")


class ProjectStatusTransitionView(APIView):
    """POST /api/v1/projects/<uuid:project_id>/transition-status/"""

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="Transition project lifecycle status",
        request=TransitionStatusRequestSerializer,
        responses={200: ProjectSerializer},
    )
    def post(self, request, project_id):
        project = _get_authorized_project(request, project_id)
        enforce_project_permission(
            user=request.user,
            organization=project.organization,
            project=project,
            permission_codename="projects.update",
            target_type="Project",
            target_id=str(project.id),
        )

        body = request.data or {}
        new_status = (body.get("status") or "").strip().lower()
        reason = (body.get("reason") or "").strip()

        if not new_status:
            raise ValidationError({"status": ["Target status is required."]})

        try:
            project = transition_project_status(
                project=project,
                new_status=new_status,
                actor=request.user,
                reason=reason,
            )
        except DjangoValidationError as exc:
            raise ValidationError({"status": exc.messages})

        return api_success(
            data=serialize_project(project),
            message=f"Project status transitioned to '{project.status}'.",
        )


class ProjectSubmitForApprovalView(APIView):
    """POST /api/v1/projects/<uuid:project_id>/submit-for-approval/"""

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="Submit project to Platform Administrator for review & approval",
        description=(
            "Submits a draft, setup, or revised project for mandatory Platform Administrator review "
            "before field submissions can begin."
        ),
        request=SubmitProjectForApprovalRequestSerializer,
        responses={200: ProjectSerializer},
    )
    def post(self, request, project_id):
        project = _get_authorized_project(request, project_id)
        enforce_project_permission(
            user=request.user,
            organization=project.organization,
            project=project,
            permission_codename="projects.update",
            target_type="Project",
            target_id=str(project.id),
        )
        body = request.data or {}
        notes = (body.get("notes") or body.get("reason") or "").strip()

        try:
            project = submit_project_for_approval(
                project=project,
                actor=request.user,
                notes=notes,
            )
        except DjangoValidationError as exc:
            raise ValidationError({"project": exc.messages})

        return api_success(
            data=serialize_project(project),
            message=f"Project '{project.code}' submitted for Platform Administrator approval.",
        )


class ProjectReviewView(APIView):
    """POST /api/v1/projects/<uuid:project_id>/review/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Projects"],
        summary="Platform Administrator review & approval decision for a project",
        description=(
            "Platform Administrators approve, request changes on, or reject engineer-created projects. "
            "Field submissions remain locked until approved."
        ),
        request=ReviewProjectRequestSerializer,
        responses={200: ProjectSerializer},
    )
    def post(self, request, project_id):
        if not getattr(request.user, "is_platform_admin", False):
            raise PermissionDenied(
                "Only a Platform Administrator can review and approve projects for field submissions."
            )

        project = get_object_or_404(
            Project.objects.select_related("organization", "reviewed_by", "created_by"),
            id=project_id,
        )
        body = request.data or {}
        decision = (body.get("decision") or "").strip().lower()
        notes = (body.get("notes") or body.get("reason") or "").strip()
        target_status = (body.get("targetStatus") or body.get("target_status") or "").strip().lower() or None

        try:
            project = review_project(
                project=project,
                admin_user=request.user,
                decision=decision,
                notes=notes,
                target_status=target_status,
            )
        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc))
        except DjangoValidationError as exc:
            raise ValidationError({"review": exc.messages})

        outcome_messages = {
            ProjectApprovalStatus.APPROVED: f"Project '{project.code}' approved for field submissions.",
            ProjectApprovalStatus.CHANGES_REQUESTED: f"Changes requested on project '{project.code}'.",
            ProjectApprovalStatus.REJECTED: f"Project '{project.code}' rejected during administrative review.",
        }
        return api_success(
            data=serialize_project(project),
            message=outcome_messages.get(project.approval_status, "Project review recorded."),
        )


class ProjectMemberListView(APIView):
    """GET, POST /api/v1/projects/<uuid:project_id>/members/"""

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="List project members",
        responses={200: ProjectMembershipSerializer(many=True)},
    )
    def get(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        members = get_project_members(
            organization_id=request.organization.id,
            project_id=project.id,
        )
        serializer = ProjectMembershipSerializer(members, many=True)
        return api_success(data=serializer.data)

    @extend_schema(
        tags=["Projects"],
        summary="Assign a tenant user to a project with an explicit RBAC role",
    )
    def post(self, request, project_id):
        project = get_object_or_404(
            Project.objects.for_organization(request.organization.id),
            id=project_id,
        )
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.manage_members",
            target_type="Project",
            target_id=str(project.id),
        )

        body = request.data or {}
        user_id = body.get("userId") or body.get("user_id")
        email = (body.get("email") or "").strip().lower()
        target_user = (
            User.objects.filter(id=user_id).first()
            if user_id
            else User.objects.filter(email=email).first()
        )
        if not target_user:
            raise ValidationError({"user": ["Target user account not found."]})

        role_slug = (body.get("role") or SystemRoleSlug.PROJECT_ENGINEER).strip()
        if role_slug == SystemRoleSlug.PLATFORM_ADMIN and not request.user.is_platform_admin:
            raise PermissionDenied("Only Platform Administrators can assign platform_admin.")

        role = Role.objects.filter(slug=role_slug).first()
        if not role:
            raise ValidationError({"role": [f"Unknown system role '{role_slug}'."]})

        dept_id = body.get("departmentId") or body.get("department_id")
        department = (
            ProjectDepartment.objects.filter(project=project, id=dept_id).first()
            if dept_id
            else None
        )

        try:
            membership = assign_user_to_project(
                project=project,
                user=target_user,
                role=role,
                department=department,
            )
        except DjangoValidationError as exc:
            raise ValidationError({"user": exc.messages})

        record_audit_event(
            action="rbac.project_member_assigned",
            target_type="ProjectMembership",
            target_id=str(membership.id),
            actor=request.user,
            organization=request.organization,
            project=project,
            metadata={
                "user_id": str(target_user.id),
                "user_email": target_user.email,
                "role": role.slug,
                "project_code": project.code,
                "department": department.code if department else None,
            },
        )
        serializer = ProjectMembershipSerializer(membership)
        return api_success(
            data=serializer.data,
            message="Project member assigned.",
            status_code=status.HTTP_201_CREATED,
        )


class ProjectMemberDetailView(APIView):
    """PATCH, DELETE /api/v1/projects/<uuid:project_id>/members/<uuid:member_id>/"""

    permission_classes = [IsTenantMember]

    def _get_project_and_membership(self, request, project_id, member_id):
        project = get_object_or_404(
            Project.objects.for_organization(request.organization.id),
            id=project_id,
        )
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.manage_members",
            target_type="Project",
            target_id=str(project.id),
        )
        membership = get_object_or_404(
            ProjectMembership.objects.select_related("user", "role", "department"),
            id=member_id,
            organization=request.organization,
            project=project,
        )
        return project, membership

    @extend_schema(tags=["Projects"], summary="Update a project member's role or department")
    def patch(self, request, project_id, member_id):
        project, membership = self._get_project_and_membership(request, project_id, member_id)
        body = request.data or {}
        previous_role = membership.role.slug if membership.role else ""

        new_role_slug = (body.get("role") or "").strip()
        if new_role_slug:
            if new_role_slug == SystemRoleSlug.PLATFORM_ADMIN and not request.user.is_platform_admin:
                raise PermissionDenied("Only Platform Administrators can assign platform_admin.")
            new_role = Role.objects.filter(slug=new_role_slug).first()
            if not new_role:
                raise ValidationError({"role": [f"Unknown system role '{new_role_slug}'."]})
            membership.role = new_role

        dept_id = body.get("departmentId") or body.get("department_id")
        if dept_id is not None:
            membership.department = (
                ProjectDepartment.objects.filter(project=project, id=dept_id).first()
                if dept_id
                else None
            )

        if "is_active" in body or "isActive" in body:
            membership.is_active = bool(body.get("isActive") if "isActive" in body else body.get("is_active"))

        membership.save()

        record_audit_event(
            action="rbac.project_role_changed",
            target_type="ProjectMembership",
            target_id=str(membership.id),
            actor=request.user,
            organization=request.organization,
            project=project,
            metadata={
                "user_email": membership.user.email,
                "previous_role": previous_role,
                "new_role": membership.role.slug,
                "is_active": membership.is_active,
                "department": membership.department.code if membership.department else None,
            },
        )
        serializer = ProjectMembershipSerializer(membership)
        return api_success(data=serializer.data, message="Project member updated.")

    @extend_schema(tags=["Projects"], summary="Remove a member from a project")
    def delete(self, request, project_id, member_id):
        project, membership = self._get_project_and_membership(request, project_id, member_id)
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])

        record_audit_event(
            action="rbac.project_member_removed",
            target_type="ProjectMembership",
            target_id=str(membership.id),
            actor=request.user,
            organization=request.organization,
            project=project,
            metadata={
                "user_email": membership.user.email,
                "role": membership.role.slug,
                "project_code": project.code,
            },
        )
        return api_success(data={"removed": True, "id": str(membership.id)}, message="Project member removed.")


class ProjectDepartmentListView(APIView):
    """GET, POST /api/v1/projects/<uuid:project_id>/departments/"""

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="List departments for a project",
        responses={200: ProjectDepartmentSerializer(many=True)},
    )
    def get(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        depts = project.projects_projectdepartment_records.all()
        return api_success(data=[serialize_project_department(d) for d in depts])

    @extend_schema(
        tags=["Projects"],
        summary="Create a new department in a project",
        request=CreateDepartmentRequestSerializer,
        responses={201: ProjectDepartmentSerializer},
    )
    def post(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.update",
            target_type="Project",
            target_id=str(project.id),
        )

        body = request.data or {}
        name = (body.get("name") or "").strip()
        code = (body.get("code") or "").strip()
        description = (body.get("description") or "").strip()

        if not name:
            raise ValidationError({"name": ["Department name is required."]})
        if not code:
            raise ValidationError({"code": ["Department code is required."]})

        try:
            department = create_project_department(
                project=project,
                name=name,
                code=code,
                description=description,
                actor=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError({"code": exc.messages})

        return api_success(
            data=serialize_project_department(department),
            message="Department created.",
            status_code=status.HTTP_201_CREATED,
        )


class ProjectDepartmentDetailView(APIView):
    """GET, PATCH, DELETE /api/v1/projects/<uuid:project_id>/departments/<uuid:department_id>/"""

    permission_classes = [IsTenantMember]

    def _get_project_and_department(self, request, project_id, department_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        department = get_object_or_404(
            project.projects_projectdepartment_records.all(),
            id=department_id,
        )
        return project, department

    @extend_schema(tags=["Projects"], summary="Get department details")
    def get(self, request, project_id, department_id):
        _, department = self._get_project_and_department(request, project_id, department_id)
        return api_success(data=serialize_project_department(department))

    @extend_schema(tags=["Projects"], summary="Update department attributes")
    def patch(self, request, project_id, department_id):
        project, department = self._get_project_and_department(request, project_id, department_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.update",
            target_type="ProjectDepartment",
            target_id=str(department.id),
        )
        body = request.data or {}
        try:
            department = update_project_department(
                department=department,
                name=body.get("name"),
                code=body.get("code"),
                description=body.get("description"),
                actor=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError({"code": exc.messages})

        return api_success(data=serialize_project_department(department), message="Department updated.")

    @extend_schema(tags=["Projects"], summary="Delete department")
    def delete(self, request, project_id, department_id):
        project, department = self._get_project_and_department(request, project_id, department_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.update",
            target_type="ProjectDepartment",
            target_id=str(department.id),
        )
        dept_id = str(department.id)
        delete_project_department(department=department, actor=request.user)
        return api_success(data={"deleted": True, "id": dept_id}, message="Department deleted.")


class ProjectActivityListView(APIView):
    """GET, POST /api/v1/projects/<uuid:project_id>/activities/"""

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="List WBS activities for a project",
        responses={200: ProjectActivitySerializer(many=True)},
    )
    def get(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        activities = project.projects_projectactivity_records.select_related(
            "department", "lead_engineer"
        ).all()

        status_filter = request.query_params.get("status")
        if status_filter:
            activities = activities.filter(status=status_filter)

        dept_filter = request.query_params.get("department_id") or request.query_params.get("department")
        if dept_filter:
            activities = activities.filter(department_id=dept_filter)

        return api_success(data=[serialize_project_activity(a) for a in activities])

    @extend_schema(
        tags=["Projects"],
        summary="Create a new WBS activity in a project",
        request=CreateActivityRequestSerializer,
        responses={201: ProjectActivitySerializer},
    )
    def post(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.update",
            target_type="Project",
            target_id=str(project.id),
        )

        body = request.data or {}
        code = (body.get("code") or "").strip()
        name = (body.get("name") or "").strip()
        if not code:
            raise ValidationError({"code": ["Activity code is required."]})
        if not name:
            raise ValidationError({"name": ["Activity name is required."]})

        dept_id = body.get("departmentId") or body.get("department_id")
        department = (
            project.projects_projectdepartment_records.filter(id=dept_id).first()
            if dept_id
            else None
        )

        lead_id = body.get("leadEngineerId") or body.get("lead_engineer_id")
        lead_engineer = User.objects.filter(id=lead_id).first() if lead_id else None

        try:
            activity = create_project_activity(
                project=project,
                code=code,
                name=name,
                department=department,
                description=body.get("description", ""),
                wbs_reference=body.get("wbsReference") or body.get("wbs_reference", ""),
                location_zone=body.get("locationZone") or body.get("location_zone", ""),
                status=body.get("status", ActivityStatus.PLANNED),
                progress_percent=int(body.get("progressPercent") or body.get("progress_percent") or 0),
                planned_start_date=body.get("plannedStartDate") or body.get("planned_start_date"),
                planned_end_date=body.get("plannedEndDate") or body.get("planned_end_date"),
                actual_start_date=body.get("actualStartDate") or body.get("actual_start_date"),
                actual_end_date=body.get("actualEndDate") or body.get("actual_end_date"),
                lead_engineer=lead_engineer,
                actor=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError({"code": exc.messages})

        return api_success(
            data=serialize_project_activity(activity),
            message="Project activity created.",
            status_code=status.HTTP_201_CREATED,
        )


class ProjectActivityDetailView(APIView):
    """GET, PATCH, DELETE /api/v1/projects/<uuid:project_id>/activities/<uuid:activity_id>/"""

    permission_classes = [IsTenantMember]

    def _get_project_and_activity(self, request, project_id, activity_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        activity = get_object_or_404(
            project.projects_projectactivity_records.select_related("department", "lead_engineer"),
            id=activity_id,
        )
        return project, activity

    @extend_schema(tags=["Projects"], summary="Get WBS activity details")
    def get(self, request, project_id, activity_id):
        _, activity = self._get_project_and_activity(request, project_id, activity_id)
        return api_success(data=serialize_project_activity(activity))

    @extend_schema(tags=["Projects"], summary="Update WBS activity details or progress")
    def patch(self, request, project_id, activity_id):
        project, activity = self._get_project_and_activity(request, project_id, activity_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.update",
            target_type="ProjectActivity",
            target_id=str(activity.id),
        )

        body = request.data or {}
        updates = {}
        for key, field in (
            ("name", "name"),
            ("description", "description"),
            ("wbsReference", "wbs_reference"),
            ("wbs_reference", "wbs_reference"),
            ("locationZone", "location_zone"),
            ("location_zone", "location_zone"),
            ("status", "status"),
            ("progressPercent", "progress_percent"),
            ("progress_percent", "progress_percent"),
            ("plannedStartDate", "planned_start_date"),
            ("planned_start_date", "planned_start_date"),
            ("plannedEndDate", "planned_end_date"),
            ("planned_end_date", "planned_end_date"),
            ("actualStartDate", "actual_start_date"),
            ("actual_start_date", "actual_start_date"),
            ("actualEndDate", "actual_end_date"),
            ("actual_end_date", "actual_end_date"),
        ):
            if key in body:
                updates[field] = body[key]

        dept_id = body.get("departmentId") or body.get("department_id")
        if dept_id is not None:
            updates["department"] = (
                project.projects_projectdepartment_records.filter(id=dept_id).first()
                if dept_id
                else None
            )

        lead_id = body.get("leadEngineerId") or body.get("lead_engineer_id")
        if lead_id is not None:
            updates["lead_engineer"] = User.objects.filter(id=lead_id).first() if lead_id else None

        activity = update_project_activity(activity=activity, actor=request.user, **updates)
        return api_success(data=serialize_project_activity(activity), message="Project activity updated.")

    @extend_schema(tags=["Projects"], summary="Delete WBS activity")
    def delete(self, request, project_id, activity_id):
        project, activity = self._get_project_and_activity(request, project_id, activity_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.update",
            target_type="ProjectActivity",
            target_id=str(activity.id),
        )
        act_id = str(activity.id)
        delete_project_activity(activity=activity, actor=request.user)
        return api_success(data={"deleted": True, "id": act_id}, message="Project activity deleted.")


class ProjectActivityHistoryView(APIView):
    """GET /api/v1/projects/<uuid:project_id>/activity-history/"""

    permission_classes = [IsTenantMember]

    @extend_schema(
        tags=["Projects"],
        summary="Get chronological activity and audit history for a project",
        responses={200: serializers.ListSerializer(child=serializers.DictField())},
    )
    def get(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        enforce_project_permission(
            user=request.user,
            organization=request.organization,
            project=project,
            permission_codename="projects.view",
            target_type="Project",
            target_id=str(project.id),
        )

        logs = AuditLog.objects.select_related("actor", "organization", "project").filter(
            project=project
        ).order_by("-created_at")[:100]

        return api_success(
            data=[serialize_audit_log(log) for log in logs],
            message="Project activity history retrieved.",
        )
