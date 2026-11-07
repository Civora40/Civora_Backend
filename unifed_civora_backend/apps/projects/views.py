"""HTTP views for Projects domain (Phase 4 / Task 002)."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.permissions import HasOrganizationCapability, IsTenantMember
from apps.access_control.services import enforce_project_permission
from apps.audit.services import record_audit_event
from apps.users.models import User
from common.responses import api_success
from .models import Project, ProjectDepartment, ProjectMembership, ProjectStatus
from .selectors import get_authorized_projects_for_user, get_project_members
from .serializers import ProjectMembershipSerializer, ProjectSerializer, serialize_project
from .services import assign_user_to_project, transition_project_status

CreateProjectRequestSerializer = inline_serializer(
    name="CreateProjectRequest",
    fields={
        "name": serializers.CharField(help_text="Project name"),
        "code": serializers.CharField(help_text="Unique project code (e.g. LCR-S3-2026)"),
        "client": serializers.CharField(help_text="Sponsoring ministry, agency, or client organization"),
        "location": serializers.CharField(required=False, allow_blank=True),
        "description": serializers.CharField(required=False, allow_blank=True),
        "status": serializers.ChoiceField(choices=["active", "planning", "setup"], required=False, default="active"),
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
        "status": serializers.ChoiceField(
            choices=["setup", "active", "on_hold", "completed", "archived"],
            required=False,
        ),
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
        # If platform admin requests all projects without explicit X-Organization-ID filter
        if request.user.is_platform_admin and not request.headers.get("X-Organization-ID"):
            projects = Project.objects.select_related("organization").prefetch_related(
                "projects_projectdepartment_records",
                "projects_projectmembership_records",
            ).all()
        else:
            projects = get_authorized_projects_for_user(
                user=request.user,
                organization_id=request.organization.id,
            ).select_related("organization").prefetch_related(
                "projects_projectdepartment_records",
                "projects_projectmembership_records",
            )
        serializer = ProjectSerializer(projects, many=True)
        return api_success(data=serializer.data)

    @extend_schema(
        tags=["Projects"],
        summary="Create a new infrastructure project",
        description="Creates a new project under the active tenant organization, assigns the creator, and logs an audit event.",
        request=CreateProjectRequestSerializer,
        responses={201: ProjectSerializer},
        examples=[
            OpenApiExample(
                "Create Highway Project Example",
                value={
                    "name": "Lagos-Calabar Coastal Highway — Section 2",
                    "code": "LCR-S2-2026",
                    "client": "Federal Ministry of Works & Housing (FMWH)",
                    "location": "Lekki Free Trade Zone, Lagos",
                    "description": "10-lane rigid concrete pavement highway corridor with service ducts.",
                    "status": "active",
                    "leadEngineer": "Engr. Tunde Bakare, COREN",
                    "lasbcaPermitNumber": "LASBCA/2026/HWY/0142",
                    "budgetAmount": "420B",
                    "currency": "₦",
                    "projectType": "Road / Highway",
                    "priority": "critical",
                    "state": "Lagos",
                    "city": "Ibeju-Lekki",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        # Verify permission: platform_admin or organization member with projects.create / projects.update
        org = request.organization
        if not request.user.is_platform_admin:
            membership = getattr(request, "organization_membership", None)
            if not membership or membership.role.slug == SystemRoleSlug.READ_ONLY_STAKEHOLDER:
                raise PermissionDenied("Forbidden: Insufficient permissions to create projects.")

        body = request.data or {}
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

        req_status = body.get("status", "active")
        db_status = ProjectStatus.SETUP if req_status == "planning" else ProjectStatus.ACTIVE

        specs_reference = body.get("specsReference") or "Eurocode 2 & FMWH Standard Specifications (C35/45)"
        project_type = body.get("projectType") or "Road / Highway"
        scope_summary = body.get("scopeSummary") or body.get("description") or ""

        project = Project.objects.create(
            organization=org,
            name=name,
            code=code,
            client_name=client_name,
            location=location_str,
            description=body.get("description") or "",
            status=db_status,
            lead_engineer_name=body.get("leadEngineer") or request.user.full_name,
            lasbca_permit_number=body.get("lasbcaPermitNumber") or "",
            budget_formatted=formatted_budget,
            specs_reference=specs_reference,
            progress_percent=0,
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

        # Assign creator to project membership automatically
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
            metadata={"code": project.code, "name": project.name},
        )

        return api_success(
            data=serialize_project(project),
            message="Project created successfully.",
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
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        return api_success(data=serialize_project(project))

    @extend_schema(
        tags=["Projects"],
        summary="Update project attributes or transition status",
        request=UpdateProjectRequestSerializer,
        responses={200: ProjectSerializer},
    )
    def patch(self, request, project_id):
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
        payload = request.data or {}

        if "status" in payload and payload["status"] != project.status:
            try:
                transition_project_status(project=project, new_status=payload["status"])
            except DjangoValidationError as exc:
                raise ValidationError({"status": exc.messages})

        for field in ("name", "location", "description", "client_name", "lead_engineer_name", "budget_formatted"):
            if field in payload:
                setattr(project, field, payload[field])
        project.save()

        record_audit_event(
            action="projects.updated",
            target_type="Project",
            target_id=str(project.id),
            actor=request.user,
            organization=request.organization,
            project=project,
            metadata={"updated_fields": list(payload.keys())},
        )
        return api_success(data=serialize_project(project), message="Project updated.")


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
