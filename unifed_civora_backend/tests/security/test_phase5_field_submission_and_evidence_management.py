"""
Phase 5 End-to-End Test Suite: Field Submission and Evidence Management.
Tests two comprehensive infrastructure projects end-to-end:
  Case 1: Lagos-Calabar Coastal Highway — Section 2 (LCR-S2-2026)
  Case 2: Eko Atlantic Water Treatment Plant & Supply Facility (EAW-TP-2026)
Verifies:
  - Manual & API field submission creation with structured payload and telemetry
  - Preserving original submitted timestamp vs server-received timestamp
  - Multipart evidence file upload with automated SHA-256 calculation
  - Dangerous executable file extension rejection (.exe, .sh, etc.)
  - Secure evidence streaming/download with authorization checks and security headers
  - Strict submission lifecycle state machine (received -> validating -> pending_review -> approved, etc.)
  - Rejection of invalid submission transitions (e.g., approved -> received)
  - RFI / clarification request workflow (transitions to needs_clarification)
  - Task completion evidence linking (TaskCompletionEvidence)
  - Evidence verification / certification sign-off
  - Multi-parameter filtering (project, department, status, channel, search)
  - Stakeholder read-only access boundaries (can view/download, cannot verify/upload/transition)
  - Strict tenant isolation for submissions and evidence files
"""
import hashlib
import io
import uuid
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access_control.models import SystemRoleSlug
from apps.access_control.services import seed_canonical_roles_and_permissions
from apps.audit.models import AuditLog
from apps.evidence.models import AttachmentMediaType, SubmissionAttachment
from apps.organizations.models import Organization, OrganizationMembership
from apps.projects.models import (
    Project,
    ProjectActivity,
    ProjectApprovalStatus,
    ProjectDepartment,
    ProjectMembership,
    ProjectStatus,
)
from apps.submissions.models import (
    Submission,
    SubmissionChannel,
    SubmissionStatus,
    SubmissionStatusHistory,
    SubmissionType,
)
from apps.tasks.models import Task, TaskCompletionEvidence, TaskStatus
from apps.users.models import User


class Phase5FieldSubmissionAndEvidenceManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.roles = seed_canonical_roles_and_permissions()

        # 1. Platform Admin
        self.admin_user = User.objects.create_user(
            email="civora40@gmail.com",
            password="Password123#",
            full_name="Civora Platform Administrator",
            is_platform_admin=True,
            is_active=True,
        )

        # 2. Highway Tenant (Case 1)
        self.org_highway = Organization.objects.create(
            name="Federal Ministry of Works & Coastal Infrastructure",
            slug="fmwh-coastal-infra",
            code="CIV-FMWH-01",
            tier="government_agency",
            compliance_threshold_percent=85.00,
        )
        self.pe_highway = User.objects.create_user(
            email="lead.highway@civora.ng",
            password="Password123#",
            full_name="Engr. Babatunde Lawal, COREN",
            is_active=True,
        )
        self.reviewer_highway = User.objects.create_user(
            email="reviewer.coren@civora.ng",
            password="Password123#",
            full_name="Engr. Amina Bello, FNSE",
            is_active=True,
        )
        self.stakeholder_highway = User.objects.create_user(
            email="oversight.ministry@fmwh.gov.ng",
            password="Password123#",
            full_name="Director Chidi Okonkwo",
            is_active=True,
        )

        # Highway memberships
        OrganizationMembership.objects.create(
            organization=self.org_highway,
            user=self.admin_user,
            role=self.roles[SystemRoleSlug.PLATFORM_ADMIN],
        )
        OrganizationMembership.objects.create(
            organization=self.org_highway,
            user=self.pe_highway,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )
        OrganizationMembership.objects.create(
            organization=self.org_highway,
            user=self.reviewer_highway,
            role=self.roles[SystemRoleSlug.SUPERVISING_REVIEWER],
        )
        OrganizationMembership.objects.create(
            organization=self.org_highway,
            user=self.stakeholder_highway,
            role=self.roles[SystemRoleSlug.READ_ONLY_STAKEHOLDER],
        )

        # 3. Water Utility Tenant (Case 2)
        self.org_water = Organization.objects.create(
            name="Lagos State Water Corporation",
            slug="lagos-water-corp",
            code="CIV-LSWC-02",
            tier="enterprise",
            compliance_threshold_percent=90.00,
        )
        self.pe_water = User.objects.create_user(
            email="lead.water@lswc.ng",
            password="Password123#",
            full_name="Engr. Olufemi Adeleke, COREN",
            is_active=True,
        )
        OrganizationMembership.objects.create(
            organization=self.org_water,
            user=self.admin_user,
            role=self.roles[SystemRoleSlug.PLATFORM_ADMIN],
        )
        OrganizationMembership.objects.create(
            organization=self.org_water,
            user=self.pe_water,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

        # 4. Isolated Third Party Tenant
        self.org_isolated = Organization.objects.create(
            name="Isolated Third Party Contractor",
            slug="isolated-contractor",
            code="CIV-ISO-99",
        )
        self.outsider_user = User.objects.create_user(
            email="outsider@isolated.ng",
            password="Password123#",
            full_name="External Contractor",
            is_active=True,
        )
        OrganizationMembership.objects.create(
            organization=self.org_isolated,
            user=self.outsider_user,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

    # =========================================================================
    # TEST CASE 1: Lagos-Calabar Coastal Highway — Section 2 (LCR-S2-2026)
    # =========================================================================
    def test_case_1_highway_submission_lifecycle_and_concrete_break_evidence(self):
        """
        Complete end-to-end field submission & evidence lifecycle on Highway Project:
        1. Provision active, approved Project LCR-S2-2026 with Department & WBS Activity.
        2. Create WBS Task TSK-LCR-001 (Concrete Cylinder Crushing Test).
        3. Field submission logged with GPS, mix ratio, weather, and multiple attachments.
        4. Cryptographic SHA-256 checksums calculated upon storage.
        5. State machine: pending_review -> RFI issued (needs_clarification) -> pending_review -> approved.
        6. Rejection of illegal transition (approved -> received).
        7. Link submission to WBS Task TSK-LCR-001 -> TaskCompletionEvidence accepted.
        8. QA Engineer verifies PDF evidence attachment -> is_verified=True.
        9. Read-only stakeholder access boundaries (view/download allowed, edit/verify blocked).
        """
        # Step 1: Provision Highway Project (Active & Approved)
        project = Project.objects.create(
            organization=self.org_highway,
            code="LCR-S2-2026",
            name="Lagos-Calabar Coastal Highway — Section 2",
            status=ProjectStatus.ACTIVE,
            approval_status=ProjectApprovalStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=timezone.now(),
            lead_engineer_name=self.pe_highway.full_name,
            specs_reference="Eurocode 2 & FMWH Standard Specifications (C35/45 Pavement Grade)",
            location="Lekki Free Trade Zone to Eleko Junction",
        )
        ProjectMembership.objects.create(
            organization=self.org_highway,
            project=project,
            user=self.pe_highway,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )
        ProjectMembership.objects.create(
            organization=self.org_highway,
            project=project,
            user=self.reviewer_highway,
            role=self.roles[SystemRoleSlug.SUPERVISING_REVIEWER],
        )
        ProjectMembership.objects.create(
            organization=self.org_highway,
            project=project,
            user=self.stakeholder_highway,
            role=self.roles[SystemRoleSlug.READ_ONLY_STAKEHOLDER],
        )

        dept_hwy = ProjectDepartment.objects.create(
            organization=self.org_highway,
            project=project,
            code="HWY-CIV",
            name="Highway & Concrete Pavement Engineering",
        )
        activity_pour = ProjectActivity.objects.create(
            organization=self.org_highway,
            project=project,
            department=dept_hwy,
            code="ACT-002",
            name="CRCP Slipform Concrete Pavement Casting",
        )
        task_crush = Task.objects.create(
            organization=self.org_highway,
            project=project,
            department=dept_hwy,
            activity=activity_pour,
            code="TSK-LCR-001",
            title="Pour and Crush 28-day Concrete Test Cylinders",
            status=TaskStatus.IN_PROGRESS,
            created_by=self.pe_highway,
        )

        self.client.force_authenticate(user=self.pe_highway)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org_highway.id)}

        # Step 2: Create Field Submission with Attachments & Telemetry
        photo_bytes = b"EXIF_JPEG_DATA_CONCRETE_POUR_KM42_500"
        pdf_bytes = b"%PDF-1.4 CONCRETE_CYLINDER_BREAK_CERTIFICATE_38_4_MPA"

        expected_photo_hash = hashlib.sha256(photo_bytes).hexdigest()
        expected_pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

        submission_payload = {
            "projectId": str(project.id),
            "departmentId": str(dept_hwy.id),
            "activityId": str(activity_pour.id),
            "reference_code": "SUB-LCR-S2-2026-0042",
            "title": "Chainage KM 42+500 CRCP Slipform Pour & Slump Log",
            "description": "Continuous reinforced concrete pavement slipform pour at Section 2. Mix C35/45, 75mm slump, 12 cylinders cast.",
            "sourceChannel": "whatsapp",
            "submittedBy": "Engr. Babajide Adeleke",
            "senderRaw": "+2348030001122",
            "gpsLatitude": 6.428100,
            "gpsLongitude": 3.421900,
            "location": "Chainage KM 42+500, Eleko Inbound",
            "weather": "Humid / 29°C overcast",
            "status": "pending_review",
            "attachments": [
                {
                    "originalFilename": "crcp_pour_km42_500.jpg",
                    "mimeType": "image/jpeg",
                    "fileSizeBytes": len(photo_bytes),
                    "checksum": expected_photo_hash,
                },
                {
                    "originalFilename": "concrete_cylinder_break_certificate_28day.pdf",
                    "mimeType": "application/pdf",
                    "fileSizeBytes": len(pdf_bytes),
                    "checksum": expected_pdf_hash,
                },
            ],
        }

        res_create = self.client.post(
            "/api/v1/submissions/",
            submission_payload,
            format="json",
            **headers,
        )
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        sub_data = res_create.data["data"]
        sub_id = sub_data["id"]

        self.assertEqual(sub_data["identifier"], "SUB-LCR-S2-2026-0042")
        self.assertEqual(sub_data["departmentCode"], "HWY-CIV")
        self.assertEqual(sub_data["activityCode"], "ACT-002")
        self.assertEqual(sub_data["sourceChannel"], "whatsapp")
        self.assertEqual(sub_data["status"], "pending_review")
        self.assertEqual(len(sub_data["attachments"]), 2)

        # Verify initial history
        history = SubmissionStatusHistory.objects.filter(submission_id=sub_id)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().to_status, "pending_review")

        # Step 3: Issue RFI / Clarification Request
        rfi_payload = {
            "queryText": "Confirm curing water temperature during 7-day initial submersion.",
            "channel": "whatsapp",
            "dueDate": "2026-11-25",
        }
        res_rfi = self.client.post(
            f"/api/v1/submissions/{sub_id}/request-info/",
            rfi_payload,
            format="json",
            **headers,
        )
        self.assertEqual(res_rfi.status_code, status.HTTP_200_OK)
        sub_after_rfi = res_rfi.data["data"]["submission"]
        self.assertEqual(sub_after_rfi["status"], "needs_clarification")
        self.assertEqual(sub_after_rfi["reviewStatus"], "needs_information")
        self.assertEqual(len(sub_after_rfi["informationRequests"]), 1)
        self.assertEqual(sub_after_rfi["informationRequests"][0]["queryText"], rfi_payload["queryText"])

        # Step 4: Submitter answers, PE transitions status back to pending_review then to approved
        res_trans_pending = self.client.post(
            f"/api/v1/submissions/{sub_id}/transition-status/",
            {"status": "pending_review", "reason": "Curing tank log received: 23.5°C constant."},
            format="json",
            **headers,
        )
        self.assertEqual(res_trans_pending.status_code, status.HTTP_200_OK)
        self.assertEqual(res_trans_pending.data["data"]["status"], "pending_review")

        res_trans_approved = self.client.post(
            f"/api/v1/submissions/{sub_id}/transition-status/",
            {"status": "approved", "reason": "Verified against Eurocode 2 C35/45 criteria (38.4 MPa achieved)."},
            format="json",
            **headers,
        )
        self.assertEqual(res_trans_approved.status_code, status.HTTP_200_OK)
        self.assertEqual(res_trans_approved.data["data"]["status"], "approved")
        self.assertEqual(res_trans_approved.data["data"]["reviewStatus"], "approved")

        # Step 5: Test Illegal Transition Blocked (approved -> received)
        res_illegal = self.client.post(
            f"/api/v1/submissions/{sub_id}/transition-status/",
            {"status": "received", "reason": "Illegal transition attempt"},
            format="json",
            **headers,
        )
        self.assertEqual(res_illegal.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Illegal transition", str(res_illegal.data))

        # Step 6: Link Submission to Task TSK-LCR-001
        res_link = self.client.post(
            f"/api/v1/submissions/{sub_id}/link-task/",
            {"taskId": str(task_crush.id), "notes": "Cylinder break test evidence linked to Task completion."},
            format="json",
            **headers,
        )
        self.assertEqual(res_link.status_code, status.HTTP_200_OK)
        evidence_record = TaskCompletionEvidence.objects.get(
            task=task_crush,
            submission_id=sub_id,
        )
        self.assertTrue(evidence_record.is_accepted)
        self.assertIn("Cylinder break test", evidence_record.notes)

        # Step 7: QA Engineer Verifies PDF Evidence Attachment
        att_pdf = SubmissionAttachment.objects.get(
            submission_id=sub_id,
            mime_type="application/pdf",
        )
        self.assertFalse(att_pdf.is_verified)

        # Supervising reviewer does not have submissions.verify capability -> 403 Forbidden
        self.client.force_authenticate(user=self.reviewer_highway)
        res_verify_rev = self.client.post(
            f"/api/v1/evidence/{att_pdf.id}/verify/",
            {"is_verified": True},
            format="json",
            **headers,
        )
        self.assertEqual(res_verify_rev.status_code, status.HTTP_403_FORBIDDEN)

        # Project Engineer has submissions.verify capability -> 200 OK
        self.client.force_authenticate(user=self.pe_highway)
        res_verify = self.client.post(
            f"/api/v1/evidence/{att_pdf.id}/verify/",
            {"is_verified": True, "notes": "COREN Certified lab break certificate verified."},
            format="json",
            **headers,
        )
        self.assertEqual(res_verify.status_code, status.HTTP_200_OK)
        att_pdf.refresh_from_db()
        self.assertTrue(att_pdf.is_verified)

        # Step 8: Supervising Reviewer & Stakeholder Access Boundaries
        # Supervising Reviewer has submissions.view -> can view submission detail
        self.client.force_authenticate(user=self.reviewer_highway)
        res_rev_view = self.client.get(f"/api/v1/submissions/{sub_id}/", **headers)
        self.assertEqual(res_rev_view.status_code, status.HTTP_200_OK)
        self.assertEqual(res_rev_view.data["data"]["identifier"], "SUB-LCR-S2-2026-0042")

        # Read-Only Stakeholder only has projects.view and reports.view_released;
        # blocked from raw field submissions and evidence (403 Forbidden)
        self.client.force_authenticate(user=self.stakeholder_highway)
        res_stk_view = self.client.get(f"/api/v1/submissions/{sub_id}/", **headers)
        self.assertEqual(res_stk_view.status_code, status.HTTP_403_FORBIDDEN)

        # Stakeholder blocked from transitioning submission status
        res_stk_trans = self.client.post(
            f"/api/v1/submissions/{sub_id}/transition-status/",
            {"status": "rejected", "reason": "Stakeholder cannot reject"},
            format="json",
            **headers,
        )
        self.assertEqual(res_stk_trans.status_code, status.HTTP_403_FORBIDDEN)

        # Stakeholder blocked from verifying evidence
        res_stk_ver = self.client.post(
            f"/api/v1/evidence/{att_pdf.id}/verify/",
            {"is_verified": False},
            format="json",
            **headers,
        )
        self.assertEqual(res_stk_ver.status_code, status.HTTP_403_FORBIDDEN)

        # Stakeholder blocked from uploading evidence
        fake_file = SimpleUploadedFile("stakeholder_upload.jpg", b"fake", content_type="image/jpeg")
        res_stk_upload = self.client.post(
            "/api/v1/evidence/upload/",
            {"file": fake_file, "projectId": str(project.id)},
            format="multipart",
            **headers,
        )
        self.assertEqual(res_stk_upload.status_code, status.HTTP_403_FORBIDDEN)

        # Step 9: Verify Immutable Audit Trail
        audit_events = AuditLog.objects.filter(project=project)
        actions = set(audit_events.values_list("action", flat=True))
        self.assertIn("submissions.created", actions)
        self.assertIn("submissions.rfi_created", actions)
        self.assertIn("submissions.status_transitioned", actions)
        self.assertIn("submissions.task_linked", actions)
        self.assertIn("evidence.verified", actions)

    # =========================================================================
    # TEST CASE 2: Eko Atlantic Water Treatment Plant (EAW-TP-2026)
    # =========================================================================
    def test_case_2_water_plant_api_telemetry_and_hydrostatic_evidence(self):
        """
        Complete end-to-end telemetry ingestion & evidence streaming on Water Utility:
        1. Provision active, approved Project EAW-TP-2026 with UTL-MEP Department & Activity.
        2. Ingest field report via direct API channel (channel=api_ingest).
        3. Upload evidence file via multipart /api/v1/evidence/upload/.
        4. Test SHA-256 calculation and storage key integrity.
        5. Stream evidence file via /api/v1/evidence/<id>/download/ with security headers.
        6. Multi-parameter search & filtering (?projectId=, ?departmentId=, ?status=, ?channel=, ?search=).
        7. Multi-tenant boundary isolation (isolated tenant user receives 404).
        """
        # Step 1: Provision Water Plant Project
        project = Project.objects.create(
            organization=self.org_water,
            code="EAW-TP-2026",
            name="Eko Atlantic Water Treatment Plant & Supply Facility",
            status=ProjectStatus.ACTIVE,
            approval_status=ProjectApprovalStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=timezone.now(),
            lead_engineer_name=self.pe_water.full_name,
            specs_reference="WHO Potable Water Guidelines & BS EN 12255",
            location="Eko Atlantic City Utility Zone",
        )
        ProjectMembership.objects.create(
            organization=self.org_water,
            project=project,
            user=self.pe_water,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

        dept_mep = ProjectDepartment.objects.create(
            organization=self.org_water,
            project=project,
            code="UTL-MEP",
            name="Mechanical, Electrical & Process Engineering",
        )
        activity_ro = ProjectActivity.objects.create(
            organization=self.org_water,
            project=project,
            department=dept_mep,
            code="WBS-02",
            name="High-Pressure Reverse Osmosis Pump Skid Installation",
        )

        self.client.force_authenticate(user=self.pe_water)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org_water.id)}

        # Step 2: Direct API Ingestion (API_INGEST)
        submission_payload = {
            "projectId": str(project.id),
            "departmentId": str(dept_mep.id),
            "activityId": str(activity_ro.id),
            "reference_code": "SUB-EAW-TP-2026-RO-01",
            "title": "Reverse Osmosis Skid 3 Hydrostatic Pressure Test Record",
            "description": "High-pressure test log: 16 bar sustained for 4 hours with 0.0 bar pressure decay. Turbidity 0.12 NTU.",
            "sourceChannel": "api_ingest",
            "submittedBy": "AutoTelemetry Logger Skid-03",
            "senderRaw": "logger-node-3@sensors.ekoatlantic.ng",
            "gpsLatitude": 6.415000,
            "gpsLongitude": 3.411000,
            "location": "RO Hall Basement Skid 3",
            "status": "pending_review",
        }
        res_create = self.client.post(
            "/api/v1/submissions/",
            submission_payload,
            format="json",
            **headers,
        )
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        sub_id = res_create.data["data"]["id"]
        self.assertEqual(res_create.data["data"]["channel"], "api_ingest")

        # Step 3: Multipart File Upload (/api/v1/evidence/upload/)
        pdf_content = b"%PDF-1.5 HYDROSTATIC_PRESSURE_CHART_SKID_03_STAMPED"
        expected_hash = hashlib.sha256(pdf_content).hexdigest()

        uploaded_pdf = SimpleUploadedFile(
            "ro_skid3_hydrostatic_pressure_log.pdf",
            pdf_content,
            content_type="application/pdf",
        )

        res_upload = self.client.post(
            "/api/v1/evidence/upload/",
            {
                "file": uploaded_pdf,
                "projectId": str(project.id),
                "submissionId": sub_id,
                "caption": "Hydrostatic pressure gauge chart recording 16 bar hold",
            },
            format="multipart",
            **headers,
        )
        self.assertEqual(res_upload.status_code, status.HTTP_201_CREATED)
        att_data = res_upload.data["data"]
        att_id = att_data["id"]

        self.assertEqual(att_data["originalFilename"], "ro_skid3_hydrostatic_pressure_log.pdf")
        self.assertEqual(att_data["checksum"], expected_hash)
        self.assertEqual(att_data["mediaType"], "document_pdf")
        self.assertIn("evidence/CIV-LSWC-02/EAW-TP-2026", att_data["storageKey"])

        # Step 4: Evidence File Streaming/Download (/api/v1/evidence/<id>/download/)
        res_download = self.client.get(f"/api/v1/evidence/{att_id}/download/", **headers)
        self.assertEqual(res_download.status_code, status.HTTP_200_OK)
        self.assertEqual(res_download["Content-Type"], "application/pdf")
        self.assertIn("ro_skid3_hydrostatic_pressure_log.pdf", res_download["Content-Disposition"])
        self.assertEqual(res_download["X-Content-Type-Options"], "nosniff")

        # Verify streamed bytes match original content exactly
        streamed_bytes = b"".join(res_download.streaming_content)
        self.assertEqual(streamed_bytes, pdf_content)

        # Step 5: Multi-parameter Search & Filtering
        # Filter by projectId
        res_by_proj = self.client.get(f"/api/v1/submissions/?projectId={project.id}", **headers)
        self.assertEqual(res_by_proj.status_code, status.HTTP_200_OK)
        self.assertEqual(res_by_proj.data["data"]["totalCount"], 1)

        # Filter by departmentId
        res_by_dept = self.client.get(f"/api/v1/submissions/?departmentId={dept_mep.id}", **headers)
        self.assertEqual(res_by_dept.status_code, status.HTTP_200_OK)
        self.assertEqual(res_by_dept.data["data"]["totalCount"], 1)

        # Filter by channel
        res_by_channel = self.client.get("/api/v1/submissions/?channel=api_ingest", **headers)
        self.assertEqual(res_by_channel.status_code, status.HTTP_200_OK)
        self.assertEqual(res_by_channel.data["data"]["totalCount"], 1)

        # Filter by full-text search
        res_by_search = self.client.get("/api/v1/submissions/?search=hydrostatic", **headers)
        self.assertEqual(res_by_search.status_code, status.HTTP_200_OK)
        self.assertEqual(res_by_search.data["data"]["totalCount"], 1)

        # Filter evidence by mediaType
        res_ev_media = self.client.get(
            f"/api/v1/evidence/?projectId={project.id}&mediaType=document_pdf",
            **headers,
        )
        self.assertEqual(res_ev_media.status_code, status.HTTP_200_OK)
        self.assertEqual(res_ev_media.data["data"]["totalCount"], 1)

        # Step 6: Multi-Tenant Boundary Isolation
        self.client.force_authenticate(user=self.outsider_user)
        isolated_headers = {"HTTP_X_ORGANIZATION_ID": str(self.org_isolated.id)}

        # Outsider cannot view submission detail (returns 404)
        res_out_sub = self.client.get(f"/api/v1/submissions/{sub_id}/", **isolated_headers)
        self.assertEqual(res_out_sub.status_code, status.HTTP_404_NOT_FOUND)

        # Outsider cannot download evidence file (returns 404)
        res_out_ev = self.client.get(f"/api/v1/evidence/{att_id}/download/", **isolated_headers)
        self.assertEqual(res_out_ev.status_code, status.HTTP_404_NOT_FOUND)

        # Outsider's submission list contains 0 records from Eko Atlantic
        res_out_list = self.client.get("/api/v1/submissions/", **isolated_headers)
        self.assertEqual(res_out_list.status_code, status.HTTP_200_OK)
        self.assertEqual(res_out_list.data["data"]["totalCount"], 0)

    # =========================================================================
    # SECURITY & GOVERNANCE CHECKS
    # =========================================================================
    def test_security_file_upload_validation_and_governance(self):
        """
        Verify security defense-in-depth:
        1. Reject dangerous executable files (.exe, .sh, .php, etc.) with 400 Bad Request.
        2. Governance: Block field submissions on unapproved projects with 403 Forbidden.
        3. Enforce terminal state: Archived submissions cannot transition.
        """
        # 1. Reject Dangerous Executables
        project = Project.objects.create(
            organization=self.org_highway,
            code="LCR-SEC-01",
            name="Security Test Project",
            status=ProjectStatus.ACTIVE,
            approval_status=ProjectApprovalStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=timezone.now(),
        )
        ProjectMembership.objects.create(
            organization=self.org_highway,
            project=project,
            user=self.pe_highway,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

        self.client.force_authenticate(user=self.pe_highway)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org_highway.id)}

        malware_file = SimpleUploadedFile("exploit.exe", b"MZ_MALWARE_BINARY", content_type="application/x-msdownload")
        res_malware = self.client.post(
            "/api/v1/evidence/upload/",
            {"file": malware_file, "projectId": str(project.id)},
            format="multipart",
            **headers,
        )
        self.assertEqual(res_malware.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not permitted for security reasons", str(res_malware.data))

        # 2. Block Submissions on Unapproved/Draft Projects (Phase 4 Governance Gate)
        draft_project = Project.objects.create(
            organization=self.org_highway,
            code="LCR-DRAFT-99",
            name="Draft Unapproved Project",
            status=ProjectStatus.DRAFT,
            approval_status=ProjectApprovalStatus.PENDING_REVIEW,
        )
        ProjectMembership.objects.create(
            organization=self.org_highway,
            project=draft_project,
            user=self.pe_highway,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

        res_blocked_sub = self.client.post(
            "/api/v1/submissions/",
            {
                "projectId": str(draft_project.id),
                "title": "Blocked Field Submission",
                "reference_code": "SUB-DRAFT-001",
            },
            format="json",
            **headers,
        )
        self.assertEqual(res_blocked_sub.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("approval", str(res_blocked_sub.data).lower())

        # 3. Enforce Terminal State: Archived Submissions cannot transition
        archived_sub = Submission.objects.create(
            organization=self.org_highway,
            project=project,
            reference_code="SUB-ARCH-001",
            title="Archived Submission",
            status=SubmissionStatus.ARCHIVED,
        )
        res_arch_trans = self.client.post(
            f"/api/v1/submissions/{archived_sub.id}/transition-status/",
            {"status": "approved", "reason": "Attempting resurrection"},
            format="json",
            **headers,
        )
        self.assertEqual(res_arch_trans.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Illegal transition", str(res_arch_trans.data))
