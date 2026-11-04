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
from apps.approvals.models import Approval, ReportReview, ReviewComment, ReviewDecision
from apps.audit.models import AuditLog
from apps.audit.services import record_audit_event
from apps.authentication.models import Account, Session
from apps.evidence.models import AttachmentMediaType, SubmissionAttachment
from apps.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationDeliveryLog,
    NotificationPreference,
)
from apps.organizations.models import Organization
from apps.organizations.services import add_user_to_organization, create_organization
from apps.projects.models import ProjectActivity, ProjectStatus
from apps.projects.selectors import get_authorized_projects_for_user
from apps.projects.services import (
    assign_user_to_project,
    create_project,
    transition_project_status,
)
from apps.reports.models import (
    Report,
    ReportEvidenceLink,
    ReportSection,
    ReportStatus,
    ReportTemplate,
    ReportVersion,
)
from apps.submissions.models import (
    Submission,
    SubmissionChannel,
    SubmissionStatus,
    SubmissionStatusHistory,
)
from apps.tasks.models import Task, TaskAssignment, TaskCompletionEvidence, TaskStatus
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
        call_command("seed_dev", demo=True)
        call_command("seed_dev")
        org_count_first = Organization.objects.count()
        role_count_first = Role.objects.count()
        # Running seed_dev a second time should not duplicate records and keeps real tenant data intact
        call_command("seed_dev")
        self.assertEqual(Organization.objects.count(), org_count_first)
        self.assertEqual(Role.objects.count(), role_count_first)
        self.assertTrue(Organization.objects.filter(code="ALPHA").exists())
        self.assertFalse(Organization.objects.filter(code__in=["JBN-NG", "ACCE-NG"]).exists())

    def test_dedicated_admin_login_and_privilege_guard(self):
        call_command("seed_dev")
        # 1. Dedicated admin (Civora40@gmail.com) can sign into /api/v1/auth/admin/login/ with v;H#bRZby'Vc#0mT
        admin_res = self.client.post(
            reverse("auth-admin-login"),
            data={"email": "Civora40@gmail.com", "password": "v;H#bRZby'Vc#0mT"},
            format="json",
        )
        self.assertEqual(admin_res.status_code, status.HTTP_200_OK)
        self.assertTrue(admin_res.data["success"])
        self.assertEqual(admin_res.data["data"]["user"]["role"], "platform_admin")
        self.assertEqual(admin_res.data["data"]["user"]["email"], "civora40@gmail.com")
        self.assertTrue(Account.objects.filter(provider_account_id="civora40@gmail.com").exists())
        self.assertTrue(Session.objects.filter(user__email="civora40@gmail.com").exists())

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

    def test_full_10_step_engineer_and_stakeholder_onboarding_flows(self):
        # 1. Engineer full 10-step progression with long COREN category & CAC persistence
        self.client.force_authenticate(user=self.pe_alpha)
        step1_res = self.client.post(
            reverse("users-onboarding"),
            data={
                "step": 1,
                "data": {
                    "firstName": "Adebayo",
                    "lastName": "Ogunlesi",
                    "displayName": "Engr. Adebayo Ogunlesi, FNSE",
                    "corenStatus": "Registered Quantity Surveyor (QSRBN/NIQS)",
                    "corenNumber": "COREN/R.77123",
                    "phone": "+234 803 456 7890",
                },
            },
            format="json",
        )
        self.assertEqual(step1_res.status_code, status.HTTP_200_OK)
        self.assertEqual(step1_res.data["data"]["nextStep"], 2)
        self.assertEqual(step1_res.data["data"]["user"]["corenStatus"], "verified")
        self.assertEqual(
            step1_res.data["data"]["user"]["corenCategory"],
            "Registered Quantity Surveyor (QSRBN/NIQS)",
        )

        step2_res = self.client.post(
            reverse("users-onboarding"),
            data={
                "step": 2,
                "data": {
                    "organization": "Tenant Alpha Construction",
                    "cacNumber": "RC-998877",
                    "officeState": "Lagos State (South West)",
                    "contractorTier": "Tier 1 Major National EPC",
                },
            },
            format="json",
        )
        self.assertEqual(step2_res.status_code, status.HTTP_200_OK)
        self.assertEqual(step2_res.data["data"]["nextStep"], 3)
        self.assertEqual(step2_res.data["data"]["user"]["cacNumber"], "RC-998877")

        # 2. Stakeholder registration & 5-step onboarding completion
        stk_reg = self.client.post(
            reverse("auth-register"),
            data={
                "firstName": "Elena",
                "lastName": "Rostova",
                "email": "elena.rostova@oversight.gov.ng",
                "password": "Pass12",  # 6-char password matching frontend validation
                "organization": "Tenant Alpha Construction",
                "jobTitle": "Oversight Director",
                "role": "read_only_stakeholder",
                "accountType": "stakeholder",
            },
            format="json",
        )
        self.assertEqual(stk_reg.status_code, status.HTTP_201_CREATED)
        self.assertEqual(stk_reg.data["data"]["nextUrl"], "/stakeholder/onboarding")
        stk_token = stk_reg.data["data"]["access"]

        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {stk_token}")
        stk_complete = self.client.post(
            reverse("users-onboarding"),
            data={
                "step": 10,
                "action": "complete",
                "data": {
                    "fullName": "Elena Rostova, MBE",
                    "organization": "Federal Ministry of Works",
                    "jobTitle": "Ministerial Observer",
                    "phone": "+234 803 456 7890",  # Same default phone must not fail unique constraint
                    "timeZone": "Europe/London (GMT+0 / UTC+1)",
                    "notificationPreferences": {
                        "notifyReleasedReports": True,
                        "notifyMilestones": True,
                    },
                },
            },
            format="json",
        )
        self.assertEqual(stk_complete.status_code, status.HTTP_200_OK)
        self.assertTrue(stk_complete.data["data"]["completed"])
        self.assertEqual(stk_complete.data["data"]["redirectUrl"], "/stakeholder")
        self.assertEqual(stk_complete.data["data"]["user"]["name"], "Elena Rostova, MBE")

    def test_engineer_and_stakeholder_profile_and_password_updates(self):
        # 1. Engineer profile, review SLA, notification & organization update via PATCH /api/v1/users/me/
        self.client.force_authenticate(user=self.pe_alpha)
        pe_patch = self.client.patch(
            reverse("users-me"),
            data={
                "fullName": "Engr. Sunkanmi Olowonishaye, FNSE",
                "jobTitle": "Principal Structural Engineer",
                "department": "Bridges & Heavy Civil",
                "organization": "Amijad Engineering Consult Ltd",
                "corenNumber": "COREN/R.88421",
                "nseNumber": "NSE-55402",
                "phone": "+234 809 112 2334",
                "reviewSla": "48h",
                "defaultSpec": "BS 8110 Structural Concrete",
                "requireLabBreakCert": True,
                "requireGeotaggedPhotos": False,
                "autoFlagLowStrength": True,
                "inboundSubmissionsAlert": True,
                "whatsappPushAlerts": False,
                "telegramBotAlerts": True,
                "criticalDiscrepancyAlerts": True,
                "weeklyComplianceDigest": False,
                "sessionTimeout": "4h",
            },
            format="json",
        )
        self.assertEqual(pe_patch.status_code, status.HTTP_200_OK)
        pe_data = pe_patch.data["data"]
        self.assertEqual(pe_data["name"], "Engr. Sunkanmi Olowonishaye, FNSE")
        self.assertEqual(pe_data["jobTitle"], "Principal Structural Engineer")
        self.assertEqual(pe_data["department"], "Bridges & Heavy Civil")
        self.assertEqual(pe_data["organization"], "Amijad Engineering Consult Ltd")
        self.assertEqual(pe_data["corenNumber"], "COREN/R.88421")
        self.assertEqual(pe_data["nseNumber"], "NSE-55402")
        self.assertEqual(pe_data["reviewSla"], "48h")
        self.assertEqual(pe_data["defaultSpec"], "BS 8110 Structural Concrete")
        self.assertFalse(pe_data["requireGeotaggedPhotos"])
        self.assertFalse(pe_data["whatsappPushAlerts"])
        self.assertEqual(pe_data["sessionTimeout"], "4h")

        # 2. Stakeholder profile, notification & regional workspace settings update via PATCH /api/v1/users/me/
        self.client.force_authenticate(user=self.stakeholder_alpha)
        stk_patch = self.client.patch(
            reverse("users-me"),
            data={
                "fullName": "Chief Tunde Bakare",
                "organization": "Lagos Metropolitan Transport Authority",
                "jobTitle": "Executive Sponsor",
                "phone": "08031234567",
                "timeZone": "WAT",
                "dateFormat": "YYYY-MM-DD",
                "highContrast": True,
                "sessionTimeout": "15",
                "notifyReleasedReports": True,
                "notifyMilestones": False,
                "notifyAnnouncements": True,
                "channelEmail": True,
                "channelInApp": False,
            },
            format="json",
        )
        self.assertEqual(stk_patch.status_code, status.HTTP_200_OK)
        stk_data = stk_patch.data["data"]
        self.assertEqual(stk_data["name"], "Chief Tunde Bakare")
        self.assertEqual(stk_data["organization"], "Lagos Metropolitan Transport Authority")
        self.assertEqual(stk_data["jobTitle"], "Executive Sponsor")
        self.assertEqual(stk_data["timeZone"], "WAT")
        self.assertEqual(stk_data["dateFormat"], "YYYY-MM-DD")
        self.assertTrue(stk_data["highContrast"])
        self.assertFalse(stk_data["notifyMilestones"])
        self.assertFalse(stk_data["channelInApp"])

        # 3. Password change validation and update
        bad_pw = self.client.post(
            reverse("users-change-password"),
            data={"currentPassword": "WrongPassword!", "newPassword": "NewSecurePassword2026!"},
            format="json",
        )
        self.assertEqual(bad_pw.status_code, status.HTTP_400_BAD_REQUEST)

        good_pw = self.client.post(
            reverse("users-change-password"),
            data={"currentPassword": "SecurePassword123!", "newPassword": "NewSecurePassword2026!"},
            format="json",
        )
        self.assertEqual(good_pw.status_code, status.HTTP_200_OK)
        self.stakeholder_alpha.refresh_from_db()
        self.assertTrue(self.stakeholder_alpha.check_password("NewSecurePassword2026!"))

    def test_phase_2_domain_models_and_cross_tenant_constraints(self):
        # 1. ProjectActivity creation & cross-tenant protection
        activity = ProjectActivity.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            code="WBS-101",
            name="Substructure Pile Cap Pouring",
            lead_engineer=self.pe_alpha,
        )
        self.assertEqual(activity.organization_id, self.org_a.id)

        with self.assertRaises(ValidationError):
            ProjectActivity.objects.create(
                organization=self.org_a,
                project=self.project_beta,  # Belongs to org_b -> must fail
                code="WBS-999",
                name="Cross-Tenant Activity Leak",
            )

        # 2. Field Operations: Submission, SubmissionStatusHistory & SubmissionAttachment
        submission = Submission.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            activity=activity,
            submitted_by=self.pe_alpha,
            reference_code="SUB-ALP-001",
            channel=SubmissionChannel.WHATSAPP,
            title="Pier 4 Concrete Cube Test Results",
            raw_text="28-day compressive strength verified at 42.5 MPa.",
        )
        history = SubmissionStatusHistory.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            submission=submission,
            from_status=SubmissionStatus.PENDING_REVIEW,
            to_status=SubmissionStatus.APPROVED,
            changed_by=self.pe_alpha,
            reason="Cube crush certificate verified against BS 8110.",
        )
        attachment = SubmissionAttachment.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            submission=submission,
            uploaded_by=self.pe_alpha,
            file_name="pier4_cube_cert.pdf",
            storage_key="org_alpha/alp_01/submissions/pier4_cube_cert.pdf",
            media_type=AttachmentMediaType.DOCUMENT_PDF,
            file_size_bytes=248910,
            sha256_checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            is_verified=True,
        )
        self.assertEqual(submission.status_history.count(), 1)
        self.assertEqual(submission.attachments.count(), 1)
        self.assertEqual(history.to_status, SubmissionStatus.APPROVED)

        # Cross-project attachment linkage must be rejected
        with self.assertRaises(ValidationError):
            SubmissionAttachment.objects.create(
                organization=self.org_b,
                project=self.project_beta,
                submission=submission,  # Belongs to project_alpha
                file_name="cross_tenant.jpg",
                storage_key="org_beta/bet_01/cross_tenant.jpg",
            )

        # 3. Tasks: Task, TaskAssignment & TaskCompletionEvidence
        task = Task.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            activity=activity,
            code="TSK-ALP-001",
            title="Verify Pier 4 Rebar Cover & Concrete Slump",
            status=TaskStatus.IN_PROGRESS,
            created_by=self.pe_alpha,
        )
        assignment = TaskAssignment.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            task=task,
            assignee=self.pe_alpha,
            assigned_by=self.pe_alpha,
        )
        evidence = TaskCompletionEvidence.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            task=task,
            submission=submission,
            attachment=attachment,
            submitted_by=self.pe_alpha,
            is_accepted=True,
        )
        self.assertEqual(task.assignments.count(), 1)
        self.assertEqual(task.completion_evidence.count(), 1)
        self.assertEqual(assignment.assignee, self.pe_alpha)
        self.assertTrue(evidence.is_accepted)

        # 4. Reporting: ReportTemplate, Report, ReportVersion, ReportSection & ReportEvidenceLink
        template = ReportTemplate.objects.create(
            organization=self.org_a,
            name="Standard Weekly Engineering Progress Template",
            code="TPL-WK-01",
            created_by=self.pe_alpha,
        )
        report = Report.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            template=template,
            reference_code="RPT-ALP-001",
            title="Week 14 Structural Progress Dossier",
            status=ReportStatus.IN_REVIEW,
            prepared_by=self.pe_alpha,
        )
        version = ReportVersion.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            report=report,
            version_number=1,
            change_summary="Initial compiled weekly dossier.",
            created_by=self.pe_alpha,
        )
        section = ReportSection.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            report=report,
            report_version=version,
            section_key="structural_progress",
            title="1. Substructure & Piling Works",
            order_index=1,
            content_markdown="Pier 4 pile cap completed and verified.",
        )
        citation = ReportEvidenceLink.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            report=report,
            section=section,
            submission=submission,
            attachment=attachment,
            citation_label="Exhibit A — Pier 4 Cube Test",
            linked_by=self.pe_alpha,
        )
        self.assertEqual(report.versions.count(), 1)
        self.assertEqual(report.sections.count(), 1)
        self.assertEqual(report.evidence_links.count(), 1)
        self.assertEqual(citation.submission, submission)

        # 5. Governance: ReportReview, ReviewComment & immutable Approval
        review = ReportReview.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            report=report,
            report_version=version,
            reviewer=self.pe_alpha,
            requested_by=self.pe_alpha,
            decision=ReviewDecision.APPROVED,
            summary_notes="All structural test certificates verified.",
        )
        comment = ReviewComment.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            review=review,
            section=section,
            author=self.pe_alpha,
            body="Confirmed 42.5 MPa cube crush strength exceeds 35 MPa spec.",
            is_resolved=True,
        )
        approval = Approval.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            report=report,
            report_version=version,
            review=review,
            approver=self.pe_alpha,
            approver_role_slug=SystemRoleSlug.PROJECT_ENGINEER,
            approver_coren_number="R.55120",
            signature_hash="sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            certification_statement="Certified compliant with BS 8110.",
        )
        self.assertEqual(review.comments.count(), 1)
        self.assertTrue(comment.is_resolved)

        # Approval sign-off must be immutable once created
        with self.assertRaises(PermissionDenied):
            approval.signature_hash = "tampered"
            approval.save()
        with self.assertRaises(PermissionDenied):
            approval.delete()

        # 6. Notifications: Notification, NotificationPreference & NotificationDeliveryLog
        pref = NotificationPreference.objects.create(
            user=self.pe_alpha,
            organization=self.org_a,
            whatsapp_enabled=True,
        )
        notif = Notification.objects.create(
            organization=self.org_a,
            project=self.project_alpha,
            recipient=self.stakeholder_alpha,
            actor=self.pe_alpha,
            event_type="report.published",
            title="Week 14 Structural Progress Dossier Published",
            body="Report RPT-ALP-001 has been approved and released.",
        )
        delivery = NotificationDeliveryLog.objects.create(
            organization=self.org_a,
            notification=notif,
            channel=NotificationChannel.EMAIL,
            recipient_address=self.stakeholder_alpha.email,
            status="delivered",
        )
        self.assertTrue(pref.whatsapp_enabled)
        self.assertEqual(notif.delivery_logs.count(), 1)
        self.assertEqual(delivery.status, "delivered")
        self.assertEqual(
            Submission.objects.for_organization(self.org_b.id).count(),
            0,
        )
        self.assertEqual(
            Submission.objects.for_project(self.org_a.id, self.project_alpha.id).count(),
            1,
        )





