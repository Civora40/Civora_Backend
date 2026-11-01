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
            raw_phone_input = (payload.get("phone") or payload.get("phone_number") or "").strip()
            cleaned_phone = raw_phone_input.replace(" ", "").replace("-", "")
            meta = dict(user.profile_metadata or {})
            if raw_phone_input:
                meta["phone"] = raw_phone_input
                user.profile_metadata = meta
            if cleaned_phone.startswith("+") and 8 <= len(cleaned_phone) <= 20:
                if not User.objects.filter(phone_number=cleaned_phone).exclude(id=user.id).exists():
                    user.phone_number = cleaned_phone
        if "timeZone" in payload or "time_zone" in payload or "timezone" in payload:
            tz_val = payload.get("timeZone") or payload.get("time_zone") or payload.get("timezone")
            if tz_val:
                user.time_zone = str(tz_val)[:64]
        if "corenNumber" in payload or "professional_registration" in payload:
            reg_val = payload.get("corenNumber") or payload.get("professional_registration")
            if reg_val:
                user.professional_registration = str(reg_val).strip()[:120]
        if "corenStatus" in payload or "coren_status" in payload:
            status_val = str(payload.get("corenStatus") or payload.get("coren_status") or "").strip()
            if status_val in ("verified", "pending", "unregistered"):
                user.coren_status = status_val
            elif status_val:
                meta = dict(user.profile_metadata or {})
                meta["corenCategory"] = status_val
                user.profile_metadata = meta
                user.coren_status = "verified" if user.professional_registration else "pending"

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
        step_data = dict(body.get("data") or {})

        meta = dict(user.profile_metadata or {})

        # Handle COREN category vs canonical coren_status ('verified' | 'pending' | 'unregistered')
        raw_coren_status = step_data.pop("corenStatus", None)
        if raw_coren_status:
            if raw_coren_status in ("verified", "pending", "unregistered"):
                user.coren_status = raw_coren_status
            else:
                meta["corenCategory"] = str(raw_coren_status).strip()
                user.coren_status = "verified" if (step_data.get("corenNumber") or user.professional_registration) else "pending"

        if step_data.get("corenCategory"):
            meta["corenCategory"] = str(step_data["corenCategory"]).strip()

        if step_data.get("organization"):
            clean_org = str(step_data["organization"]).strip()
            step_data["organization"] = clean_org
            meta["registeredOrganization"] = clean_org

        if step_data.get("cacNumber"):
            step_data["cacNumber"] = str(step_data["cacNumber"]).strip()

        meta.update(step_data)
        user.profile_metadata = meta

        # Identity fields (supports both firstName/lastName and fullName/name from StakeholderOnboarding)
        if step_data.get("firstName"):
            user.first_name = str(step_data["firstName"]).strip()[:120]
        if step_data.get("lastName"):
            user.last_name = str(step_data["lastName"]).strip()[:120]

        if step_data.get("firstName") or step_data.get("lastName"):
            user.full_name = f"{user.first_name} {user.last_name}".strip()[:255]
        elif step_data.get("fullName") or step_data.get("name"):
            full_str = str(step_data.get("fullName") or step_data.get("name")).strip()
            if full_str:
                user.full_name = full_str[:255]
                parts = full_str.split(" ", 1)
                user.first_name = parts[0][:120]
                user.last_name = (parts[1] if len(parts) > 1 else "")[:120]

        if step_data.get("displayName"):
            user.display_name = str(step_data["displayName"]).strip()[:255]
        elif not user.display_name and user.full_name:
            user.display_name = user.full_name

        if step_data.get("jobTitle") or step_data.get("title"):
            user.job_title = str(step_data.get("jobTitle") or step_data.get("title")).strip()[:150]
        elif step_data.get("primaryResponsibility"):
            user.job_title = str(step_data["primaryResponsibility"]).strip()[:150]

        if step_data.get("department"):
            user.department = str(step_data["department"]).strip()[:150]

        if step_data.get("timeZone") or step_data.get("timezone"):
            user.time_zone = str(step_data.get("timeZone") or step_data.get("timezone")).strip()[:64]

        if step_data.get("phone"):
            raw_phone_input = str(step_data["phone"]).strip()
            cleaned_phone = raw_phone_input.replace(" ", "").replace("-", "")
            if cleaned_phone.startswith("+") and 8 <= len(cleaned_phone) <= 20:
                if not User.objects.filter(phone_number=cleaned_phone).exclude(id=user.id).exists():
                    user.phone_number = cleaned_phone

        if step_data.get("corenNumber"):
            user.professional_registration = str(step_data["corenNumber"]).strip()[:120]
            if user.coren_status == "unregistered":
                user.coren_status = "verified"

        # Persist CAC number onto user's active organization if blank
        if step_data.get("cacNumber"):
            active_membership = (
                user.organization_memberships.select_related("organization")
                .filter(is_active=True)
                .first()
            )
            if active_membership and active_membership.organization and not active_membership.organization.cac_number:
                active_membership.organization.cac_number = str(step_data["cacNumber"]).strip()[:64]
                active_membership.organization.save(update_fields=["cac_number", "updated_at"])

        if action == "complete" or step == 10:
            user.onboarding_completed_at = timezone.now()
            user.onboarding_step = 10
            user.account_status = "active"
            user.save()
            user_payload = serialize_civora_user(user)
            target_dashboard = (
                "/stakeholder"
                if user_payload.get("role") == "read_only_stakeholder"
                else "/dashboard"
            )
            record_audit_event(
                action="users.onboarding_completed",
                target_type="User",
                target_id=str(user.id),
                actor=user,
                metadata={
                    "role": user_payload.get("role"),
                    "coren_number": user.professional_registration,
                    "organization": user_payload.get("organization"),
                },
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
