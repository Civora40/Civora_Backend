"""HTTP views for Users domain (Phase 3 / Task 002) with Swagger/OpenAPI schemas."""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from apps.audit.services import record_audit_event
from common.responses import api_success
from .models import User
from .serializers import UserProfileSerializer, serialize_civora_user


class CurrentUserProfileView(APIView):
    """GET, PATCH /api/v1/users/me/"""

    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        tags=["Users & Onboarding"],
        summary="Get Current Authenticated User Profile (`/users/me/`)",
        responses={200: UserProfileSerializer},
    )
    def get(self, request):
        return api_success(data=serialize_civora_user(request.user))

    @extend_schema(
        tags=["Users & Onboarding"],
        summary="Update Current User Profile",
        request=inline_serializer(
            name="UserProfileUpdateRequest",
            fields={
                "firstName": serializers.CharField(required=False),
                "lastName": serializers.CharField(required=False),
                "displayName": serializers.CharField(required=False),
                "jobTitle": serializers.CharField(required=False),
                "department": serializers.CharField(required=False),
                "phone": serializers.CharField(required=False),
                "timeZone": serializers.CharField(required=False),
                "corenNumber": serializers.CharField(required=False),
                "corenStatus": serializers.CharField(required=False),
            },
        ),
        responses={200: UserProfileSerializer},
    )
    def patch(self, request):
        user: User = request.user
        payload = request.data or {}
        if "firstName" in payload or "first_name" in payload:
            user.first_name = payload.get("firstName") or payload.get("first_name") or user.first_name
        if "lastName" in payload or "last_name" in payload:
            user.last_name = payload.get("lastName") or payload.get("last_name") or user.last_name
        if "full_name" in payload or "name" in payload:
            user.full_name = payload.get("full_name") or payload.get("name") or user.full_name
        elif user.first_name or user.last_name:
            user.full_name = f"{user.first_name} {user.last_name}".strip()
        if "displayName" in payload or "display_name" in payload:
            user.display_name = payload.get("displayName") or payload.get("display_name") or user.display_name
        if "jobTitle" in payload or "job_title" in payload or "title" in payload:
            user.job_title = payload.get("jobTitle") or payload.get("job_title") or payload.get("title") or user.job_title
        if "department" in payload:
            user.department = payload.get("department") or user.department
        if "phone" in payload or "phone_number" in payload:
            raw_phone = (payload.get("phone") or payload.get("phone_number") or "").strip().replace(" ", "")
            if raw_phone:
                user.phone_number = raw_phone
        if "timeZone" in payload or "time_zone" in payload:
            user.time_zone = payload.get("timeZone") or payload.get("time_zone") or user.time_zone
        if "corenNumber" in payload or "professional_registration" in payload:
            user.professional_registration = (
                payload.get("corenNumber")
                or payload.get("professional_registration")
                or user.professional_registration
            )
        if "corenStatus" in payload or "coren_status" in payload:
            user.coren_status = payload.get("corenStatus") or payload.get("coren_status") or user.coren_status

        user.save()
        return api_success(data=serialize_civora_user(user), message="Profile updated.")


class UserOnboardingView(APIView):
    """
    GET, POST /api/v1/users/onboarding/
    Supports the 10-step Nigerian Construction & COREN/CAC onboarding flow in Civora Frontend.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Users & Onboarding"],
        summary="Get 10-Step Onboarding State",
        responses={
            200: inline_serializer(
                name="OnboardingStateResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "data": serializers.DictField(),
                },
            )
        },
    )
    def get(self, request):
        user: User = request.user
        completed = bool(user.onboarding_completed_at)
        current_step = 10 if completed else (user.onboarding_step or 1)
        return api_success(
            data={
                "user": serialize_civora_user(user),
                "completed": completed,
                "currentStep": current_step,
            }
        )

    @extend_schema(
        tags=["Users & Onboarding"],
        summary="Advance or Complete 10-Step Onboarding Flow",
        request=inline_serializer(
            name="OnboardingStepRequest",
            fields={
                "step": serializers.IntegerField(min_value=1, max_value=10),
                "action": serializers.CharField(required=False, help_text="'complete' on final step 10"),
                "data": serializers.DictField(required=False),
            },
        ),
        responses={
            200: inline_serializer(
                name="OnboardingAdvanceResponse",
                fields={
                    "success": serializers.BooleanField(default=True),
                    "data": serializers.DictField(),
                },
            )
        },
        examples=[
            OpenApiExample(
                "Complete Onboarding (Step 10)",
                value={
                    "step": 10,
                    "action": "complete",
                    "data": {"corenNumber": "R.55120", "corenStatus": "verified"},
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        user: User = request.user
        body = request.data or {}
        step = body.get("step")
        action = body.get("action")
        step_data = body.get("data") or {}

        meta = dict(user.profile_metadata or {})
        meta.update(step_data)
        user.profile_metadata = meta

        if step_data.get("firstName"):
            user.first_name = step_data["firstName"]
        if step_data.get("lastName"):
            user.last_name = step_data["lastName"]
        if user.first_name or user.last_name:
            user.full_name = f"{user.first_name} {user.last_name}".strip()
        if step_data.get("displayName"):
            user.display_name = step_data["displayName"]
        if step_data.get("jobTitle"):
            user.job_title = step_data["jobTitle"]
        if step_data.get("department"):
            user.department = step_data["department"]
        if step_data.get("timeZone"):
            user.time_zone = step_data["timeZone"]
        if step_data.get("corenNumber"):
            user.professional_registration = step_data["corenNumber"]
        if step_data.get("corenStatus"):
            user.coren_status = step_data["corenStatus"]

        if action == "complete" or step == 10:
            user.onboarding_completed_at = timezone.now()
            user.onboarding_step = 10
            user.account_status = "active"
            user.save()
            user_payload = serialize_civora_user(user)
            target_dashboard = (
                "/dashboard/stakeholder"
                if user_payload.get("role") == "read_only_stakeholder"
                else "/dashboard"
            )
            record_audit_event(
                action="users.onboarding_completed",
                target_type="User",
                target_id=str(user.id),
                actor=user,
            )
            return api_success(
                data={
                    "user": user_payload,
                    "completed": True,
                    "redirectUrl": target_dashboard,
                }
            )

        next_step = (int(step) + 1) if isinstance(step, int) and 1 <= step <= 9 else (user.onboarding_step + 1)
        user.onboarding_step = min(next_step, 10)
        user.save()
        return api_success(
            data={
                "user": serialize_civora_user(user),
                "nextStep": user.onboarding_step,
            }
        )


class UserListView(APIView):
    """
    GET /api/v1/users/
    Returns user registry for Platform Admins or members within the active tenant organization.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        tags=["Users & Onboarding"],
        summary="List Platform or Tenant Users",
        responses={200: UserProfileSerializer(many=True)},
    )
    def get(self, request):
        if request.user.is_platform_admin:
            qs = User.objects.prefetch_related(
                "organization_memberships__organization",
                "organization_memberships__role",
                "project_memberships__project",
            ).all()
        else:
            org = getattr(request, "organization", None)
            if not org:
                return api_success(data=[])
            qs = User.objects.filter(organization_memberships__organization=org).distinct()

        data = [serialize_civora_user(u) for u in qs]
        return api_success(data=data)


class UserToggleStatusView(APIView):
    """
    POST /api/v1/users/<uuid:user_id>/toggle-status/
    Suspends or reactivates a user account and emits an immutable audit event.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        tags=["Users & Onboarding"],
        summary="Suspend or Reactivate User Account (Admin Only)",
        request=None,
        responses={200: UserProfileSerializer},
    )
    def post(self, request, user_id):
        if not request.user.is_platform_admin:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only platform administrators can suspend or reactivate accounts.")

        target_user = get_object_or_404(User, id=user_id)
        if target_user.account_status == "active" and target_user.is_active:
            target_user.account_status = "suspended"
            target_user.is_active = False
            action_code = "USER_ACCOUNT_SUSPENDED"
        else:
            target_user.account_status = "active"
            target_user.is_active = True
            action_code = "USER_ACCOUNT_REACTIVATED"

        target_user.save(update_fields=["account_status", "is_active", "updated_at"])

        record_audit_event(
            action=action_code,
            target_type="CivoraUser",
            target_id=str(target_user.id),
            actor=request.user,
            organization=getattr(request, "organization", None),
            metadata={
                "email": target_user.email,
                "new_status": target_user.account_status,
            },
        )
        return api_success(
            data=serialize_civora_user(target_user),
            message=f"User status updated to {target_user.account_status}.",
        )
