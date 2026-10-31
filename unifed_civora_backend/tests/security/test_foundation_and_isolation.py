"""
Phase 1, 2 & Task 001 Acceptance Tests:
1. Health check endpoint and database connectivity
2. Environment validation failure handling
3. Repeatable development seed command (`seed_dev`)
4. Multi-tenant organization and project data isolation
5. RBAC capability evaluation and separation of duties
6. Project lifecycle state machine rules
7. Immutable AuditLog enforcement
"""
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.selectors import (
    user_has_organization_permission,
    user_has_project_permission,
)
from apps.access_control.services import seed_canonical_roles_and_permissions
from apps.audit.models import AuditLog
from apps.audit.services import record_audit_event
from apps.organizations.models import Organization
from apps.organizations.services import add_user_to_organization, create_organization
from apps.projects.models import ProjectStatus
from apps.projects.selectors import get_authorized_projects_for_user
from apps.projects.services import (
    assign_user_to_project,
    create_project,
    transition_project_status,
)
from apps.users.services import create_user_account
from config.settings.env import validate_environment


class FoundationAndTenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.roles = seed_canonical_roles_and_permissions()

        # Tenant A
        self.org_a = create_organization(
            name="Tenant Alpha Construction",
            slug="tenant-alpha",
            code="ALPHA",
        )
        # Tenant B
        self.org_b = create_organization(
            name="Tenant Beta Engineering",
            slug="tenant-beta",
            code="BETA",
        )

        # Users
        self.pe_alpha = create_user_account(
            email="pe@alpha.io",
            full_name="Alpha Project Engineer",
            password="SecurePassword123!",
        )
        self.stakeholder_alpha = create_user_account(
            email="stakeholder@alpha.io",
            full_name="Alpha Read-Only Stakeholder",
            password="SecurePassword123!",
        )
        self.pe_beta = create_user_account(
            email="pe@beta.io",
            full_name="Beta Project Engineer",
            password="SecurePassword123!",
        )

        # Organization memberships
        add_user_to_organization(
            organization=self.org_a,
            user=self.pe_alpha,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )
        add_user_to_organization(
            organization=self.org_a,
            user=self.stakeholder_alpha,
            role=self.roles[SystemRoleSlug.READ_ONLY_STAKEHOLDER],
        )
        add_user_to_organization(
            organization=self.org_b,
            user=self.pe_beta,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

        # Projects
        self.project_alpha = create_project(
            organization=self.org_a,
            name="Alpha Bridge Highway",
            code="ALP-01",
            created_by=self.pe_alpha,
        )
        self.project_beta = create_project(
            organization=self.org_b,
            name="Beta Port Terminal",
            code="BET-01",
            created_by=self.pe_beta,
        )

        # Project memberships
        assign_user_to_project(
            project=self.project_alpha,
            user=self.pe_alpha,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )
        assign_user_to_project(
            project=self.project_alpha,
            user=self.stakeholder_alpha,
            role=self.roles[SystemRoleSlug.READ_ONLY_STAKEHOLDER],
        )
        assign_user_to_project(
            project=self.project_beta,
            user=self.pe_beta,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

    def test_health_check_endpoint_returns_200_and_db_connected(self):
        response = self.client.get(reverse("api-health"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertTrue(response.data["data"]["database"]["connected"])

    def test_environment_validation_rejects_invalid_env_or_weak_production_secrets(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_environment("invalid_env", "some-secret-key")

        with self.assertRaises(ImproperlyConfigured):
            validate_environment("production", "short-key", "postgres://user:pass@localhost:5432/db")

        with self.assertRaises(ImproperlyConfigured):
            validate_environment("production", "a" * 40, "sqlite:///db.sqlite3")

    def test_cross_tenant_project_isolation_enforced(self):
        # Alpha engineer can only see Alpha projects, never Beta projects
        alpha_projects = get_authorized_projects_for_user(
            user=self.pe_alpha,
            organization_id=self.org_a.id,
        )
        self.assertEqual(list(alpha_projects), [self.project_alpha])

        # Alpha engineer querying with Beta's organization_id gets empty queryset
        cross_tenant_query = get_authorized_projects_for_user(
            user=self.pe_alpha,
            organization_id=self.org_b.id,
        )
        self.assertEqual(cross_tenant_query.count(), 0)

        # Cannot assign Beta user to Alpha project
        with self.assertRaises(ValidationError):
            assign_user_to_project(
                project=self.project_alpha,
                user=self.pe_beta,
                role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
            )

    def test_api_spoofed_organization_header_is_rejected(self):
        self.client.force_authenticate(user=self.pe_alpha)
        # Alpha user spoofs X-Organization-ID for Beta organization
        response = self.client.get(
            reverse("project-list"),
            HTTP_X_ORGANIZATION_ID=str(self.org_b.id),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Valid tenant header returns Alpha's project
        valid_response = self.client.get(
            reverse("project-list"),
            HTTP_X_ORGANIZATION_ID=str(self.org_a.id),
        )
        self.assertEqual(valid_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(valid_response.data["data"]), 1)
        self.assertEqual(valid_response.data["data"][0]["code"], "ALP-01")

    def test_rbac_capabilities_and_separation_of_duties(self):
        # Project Engineer can draft reports & verify submissions, but CANNOT approve or release final reports
        self.assertTrue(
            user_has_project_permission(
                self.pe_alpha, self.org_a.id, self.project_alpha.id, "submissions.verify"
            )
        )
        self.assertTrue(
            user_has_project_permission(
                self.pe_alpha, self.org_a.id, self.project_alpha.id, "reports.draft"
            )
        )
        self.assertFalse(
            user_has_project_permission(
                self.pe_alpha, self.org_a.id, self.project_alpha.id, "reports.approve"
            )
        )
        self.assertFalse(
            user_has_project_permission(
                self.pe_alpha, self.org_a.id, self.project_alpha.id, "reports.release"
            )
        )

        # Read-only stakeholder can view released reports, but cannot view raw submissions or draft reports
        self.assertTrue(
            user_has_project_permission(
                self.stakeholder_alpha, self.org_a.id, self.project_alpha.id, "reports.view_released"
            )
        )
        self.assertFalse(
            user_has_project_permission(
                self.stakeholder_alpha, self.org_a.id, self.project_alpha.id, "submissions.view"
            )
        )

    def test_project_lifecycle_state_machine_rejects_invalid_transitions(self):
        # Starts in DRAFT; cannot jump directly to COMPLETED
        with self.assertRaises(ValidationError):
            transition_project_status(
                project=self.project_alpha,
                new_status=ProjectStatus.COMPLETED,
            )

        # Valid transition: DRAFT -> SETUP -> ACTIVE
        transition_project_status(project=self.project_alpha, new_status=ProjectStatus.SETUP)
        transition_project_status(project=self.project_alpha, new_status=ProjectStatus.ACTIVE)
        self.assertEqual(self.project_alpha.status, ProjectStatus.ACTIVE)

    def test_audit_log_is_append_only_and_immutable(self):
        entry = record_audit_event(
            action="project.created",
            target_type="Project",
            target_id=str(self.project_alpha.id),
            actor=self.pe_alpha,
            organization=self.org_a,
            project=self.project_alpha,
        )
        with self.assertRaises(PermissionDenied):
            entry.action = "tampered.action"
            entry.save()

        with self.assertRaises(PermissionDenied):
            entry.delete()

    def test_seed_dev_command_is_idempotent(self):
        call_command("seed_dev")
        org_count_first = Organization.objects.count()
        role_count_first = Role.objects.count()
        # Running seed_dev a second time should not duplicate records
        call_command("seed_dev")
        self.assertEqual(Organization.objects.count(), org_count_first)
        self.assertEqual(Role.objects.count(), role_count_first)

    def test_dedicated_admin_login_and_privilege_guard(self):
        call_command("seed_dev")
        # 1. Dedicated admin can sign into /api/v1/auth/admin/login/
        admin_res = self.client.post(
            reverse("auth-admin-login"),
            data={"email": "admin@civora.internal", "password": "Civora2026!"},
            format="json",
        )
        self.assertEqual(admin_res.status_code, status.HTTP_200_OK)
        self.assertTrue(admin_res.data["success"])
        self.assertEqual(admin_res.data["data"]["user"]["role"], "platform_admin")

        # 2. Non-admin project engineer is rejected from /api/v1/auth/admin/login/ with 403
        pe_admin_attempt = self.client.post(
            reverse("auth-admin-login"),
            data={"email": "pe@alpha.io", "password": "SecurePassword123!"},
            format="json",
        )
        self.assertEqual(pe_admin_attempt.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_registration_prevents_self_assigned_platform_admin(self):
        # Attempt to register with role='platform_admin' must downgrade to 'project_engineer'
        reg_res = self.client.post(
            reverse("auth-register"),
            data={
                "firstName": "Kemi",
                "lastName": "Alabi",
                "email": "kemi.alabi@newbuild.ng",
                "password": "StrongPassword2026!",
                "organization": "Tenant Alpha Construction",
                "jobTitle": "Site Engineer",
                "role": "platform_admin",
            },
            format="json",
        )
        self.assertEqual(reg_res.status_code, status.HTTP_201_CREATED)
        user_payload = reg_res.data["data"]["user"]
        self.assertEqual(user_payload["role"], "project_engineer")
        self.assertFalse(user_payload["is_platform_admin"])

    def test_onboarding_progression_and_project_creation_api(self):
        self.client.force_authenticate(user=self.pe_alpha)
        # Complete onboarding step 10
        onb_res = self.client.post(
            reverse("users-onboarding"),
            data={
                "step": 10,
                "action": "complete",
                "data": {"corenNumber": "R.55120", "corenStatus": "verified"},
            },
            format="json",
        )
        self.assertEqual(onb_res.status_code, status.HTTP_200_OK)
        self.assertTrue(onb_res.data["data"]["completed"])
        self.assertEqual(onb_res.data["data"]["user"]["corenNumber"], "R.55120")

        # Create a new project via POST /api/v1/projects/
        proj_res = self.client.post(
            reverse("project-list"),
            data={
                "name": "Third Mainland Structural Rehabilitation",
                "code": "TMB-REHAB-01",
                "client": "Federal Ministry of Works",
                "city": "Lagos Island",
                "state": "Lagos",
                "country": "Nigeria",
                "budgetAmount": "52.4B",
            },
            format="json",
            HTTP_X_ORGANIZATION_ID=str(self.org_a.id),
        )
        self.assertEqual(proj_res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(proj_res.data["data"]["code"], "TMB-REHAB-01")
        self.assertEqual(proj_res.data["data"]["client"], "Federal Ministry of Works")


