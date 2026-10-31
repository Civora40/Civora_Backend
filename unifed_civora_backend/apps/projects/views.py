"""HTTP views for Projects domain (Phase 4 / Task 002)."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.permissions import HasOrganizationCapability, IsTenantMember
from apps.audit.services import record_audit_event
from common.responses import api_success
from .models import Project, ProjectStatus
from .selectors import get_authorized_projects_for_user, get_project_members
from .serializers import ProjectMembershipSerializer, ProjectSerializer, serialize_project
from .services import assign_user_to_project, transition_project_status


class ProjectListView(APIView):
    """
    GET, POST /api/v1/projects/
    Lists tenant projects authorized for the current user or creates a new project.
    """

    permission_classes = [IsTenantMember]

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
            specs_reference=body.get("specsReference") or "Eurocode 2 & FMWH Standard Specifications (C35/45)",
            progress_percent=0,
            project_metadata={
                "projectType": body.get("projectType") or "Road / Highway",
                "contractRef": body.get("contractRef") or "",
                "priority": body.get("priority") or "normal",
                "country": body.get("country") or "Nigeria",
                "state": body.get("state") or "",
                "city": body.get("city") or "",
                "siteAddress": body.get("siteAddress") or "",
                "coordinates": body.get("coordinates") or "",
                "startDate": body.get("startDate") or "",
                "completionDate": body.get("completionDate") or "",
                "scopeSummary": body.get("scopeSummary") or "",
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

    def get(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
        return api_success(data=serialize_project(project))

    def patch(self, request, project_id):
        qs = get_authorized_projects_for_user(
            user=request.user,
            organization_id=request.organization.id,
        )
        project = get_object_or_404(qs, id=project_id)
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
    """GET /api/v1/projects/<uuid:project_id>/members/"""

    permission_classes = [IsTenantMember]

    def get(self, request, project_id):
        members = get_project_members(
            organization_id=request.organization.id,
            project_id=project_id,
        )
        serializer = ProjectMembershipSerializer(members, many=True)
        return api_success(data=serializer.data)
