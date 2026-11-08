"""
Phase 4 End-to-End Test Suite: Organization and Project Management.
Tests two comprehensive infrastructure projects end-to-end:
  Case 1: Lagos-Calabar Coastal Highway — Section 2 (LCR-S2-2026)
  Case 2: Eko Atlantic Water Treatment Plant & Supply Facility (EAW-TP-2026)
Verifies:
  - Organization provisioning, profile & governance configuration
  - Secure invitation issuance, replay protection, and acceptance
  - Project creation, configuration & specification folder generation
  - Department and work category management (CRUD & uniqueness)
  - WBS Activity management (CRUD, progress tracking, status)
  - Project Engineer and Stakeholder role assignment & department binding
  - Strict lifecycle state machine enforcement:
      Draft -> Setup -> Active -> On Hold -> Completed -> Archived
  - Rejection of invalid lifecycle transitions
  - Terminal state validation (Archived)
  - Tenant isolation & cross-tenant access rejection
  - Stakeholder read-only access boundaries
  - Immutable project activity history & audit trail
"""
import uuid
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from apps.access_control.models import Role, SystemRoleSlug
from apps.access_control.services import seed_canonical_roles_and_permissions
from apps.audit.models import AuditLog
from apps.organizations.models import Organization, OrganizationInvitation, OrganizationMembership
from apps.projects.models import (
    ActivityStatus,
    Project,
    ProjectActivity,
    ProjectDepartment,
    ProjectMembership,
    ProjectStatus,
)
from apps.users.models import User


class Phase4OrganizationAndProjectManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.roles = seed_canonical_roles_and_permissions()

        # 1. Platform Admin
        self.admin_user = User.objects.create_user(
            email="civora40@gmail.com",
            password="v;H#bRZby'Vc#0mT",
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

        # 4. Outsider user (Tenant Isolation test)
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
    def test_case_1_highway_project_end_to_end_lifecycle(self):
        """
        Complete end-to-end lifecycle of Highway Project (LCR-S2-2026):
        Creation -> 4 Departments -> Member Bindings -> Illegal Transition Blocked ->
        Draft -> Setup -> Active -> WBS Activities -> On Hold -> Active ->
        Activity Progress -> Completed -> Stakeholder Read-Only Boundary ->
        Audit Trail History.
        """
        self.client.force_authenticate(user=self.pe_highway)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org_highway.id)}

        # 1. Create Project in Draft Status
        create_payload = {
            "name": "Lagos-Calabar Coastal Highway — Section 2",
            "code": "LCR-S2-2026",
            "client": "Federal Ministry of Works & Housing (FMWH)",
            "location": "Lekki Free Trade Zone to Eleko Junction, Lagos State",
            "description": "10-lane rigid concrete pavement highway corridor with coastal erosion protection groynes.",
            "status": "draft",
            "leadEngineer": self.pe_highway.full_name,
            "lasbcaPermitNumber": "LASBCA/2026/HWY/0142",
            "budgetAmount": "420B",
            "currency": "₦",
            "specsReference": "Eurocode 2 & FMWH Standard Specifications (C35/45 Pavement Grade)",
            "projectType": "Highway & Coastal Defense Corridor",
            "priority": "critical",
            "state": "Lagos",
            "city": "Ibeju-Lekki",
        }
        res = self.client.post("/api/v1/projects/", create_payload, format="json", **headers)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        project_data = res.data["data"]
        project_id = project_data["id"]
        self.assertEqual(project_data["code"], "LCR-S2-2026")
        self.assertEqual(project_data["status"], "draft")
        self.assertEqual(project_data["specificationFolder"]["code"], "LCR-S2-2026-SPEC")

        # 2. Add 4 Specialized Engineering Departments
        dept_defs = [
            ("Highway & Concrete Pavement Engineering", "HWY-CIV", "Rigid CRCP pavement slipform casting"),
            ("Coastal Geotechnical & Marine Reclamation", "GEO-MAR", "Subgrade geotextile and hydraulic sandfill"),
            ("Quality Assurance & Material Testing", "QA-MAT", "Compressive cylinder breaks & sieve analysis"),
            ("Health, Safety & Environmental Protection", "HSE-ENV", "Coastal erosion & marine sanctuary safety"),
        ]
        created_depts = {}
        for name, code, desc in dept_defs:
            d_res = self.client.post(
                f"/api/v1/projects/{project_id}/departments/",
                {"name": name, "code": code, "description": desc},
                format="json",
                **headers,
            )
            self.assertEqual(d_res.status_code, status.HTTP_201_CREATED, d_res.data)
            created_depts[code] = d_res.data["data"]

        # Verify duplicate department code is rejected
        dup_dept = self.client.post(
            f"/api/v1/projects/{project_id}/departments/",
            {"name": "Duplicate HWY", "code": "HWY-CIV", "description": "Dup"},
            format="json",
            **headers,
        )
        self.assertEqual(dup_dept.status_code, status.HTTP_400_BAD_REQUEST)

        # List departments
        list_depts = self.client.get(f"/api/v1/projects/{project_id}/departments/", **headers)
        self.assertEqual(list_depts.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_depts.data["data"]), 4)

        # Update department
        hwy_dept_id = created_depts["HWY-CIV"]["id"]
        patch_dept = self.client.patch(
            f"/api/v1/projects/{project_id}/departments/{hwy_dept_id}/",
            {"description": "Updated CRCP specification to 40MPa flexural design"},
            format="json",
            **headers,
        )
        self.assertEqual(patch_dept.status_code, status.HTTP_200_OK)
        self.assertIn("40MPa", patch_dept.data["data"]["description"])

        # 3. Assign Team Members with Department Bindings
        # Assign Supervising Reviewer to QA-MAT
        qa_dept_id = created_depts["QA-MAT"]["id"]
        rev_assign = self.client.post(
            f"/api/v1/projects/{project_id}/members/",
            {
                "userId": str(self.reviewer_highway.id),
                "role": SystemRoleSlug.SUPERVISING_REVIEWER,
                "departmentId": qa_dept_id,
            },
            format="json",
            **headers,
        )
        self.assertEqual(rev_assign.status_code, status.HTTP_201_CREATED, rev_assign.data)
        self.assertEqual(rev_assign.data["data"]["role_slug"], SystemRoleSlug.SUPERVISING_REVIEWER)

        # Assign Stakeholder
        stk_assign = self.client.post(
            f"/api/v1/projects/{project_id}/members/",
            {
                "userId": str(self.stakeholder_highway.id),
                "role": SystemRoleSlug.READ_ONLY_STAKEHOLDER,
            },
            format="json",
            **headers,
        )
        self.assertEqual(stk_assign.status_code, status.HTTP_201_CREATED, stk_assign.data)
        self.assertEqual(stk_assign.data["data"]["role_slug"], SystemRoleSlug.READ_ONLY_STAKEHOLDER)

        # Attempt privilege escalation: PE cannot assign platform_admin
        escalate = self.client.post(
            f"/api/v1/projects/{project_id}/members/",
            {
                "userId": str(self.reviewer_highway.id),
                "role": SystemRoleSlug.PLATFORM_ADMIN,
            },
            format="json",
            **headers,
        )
        self.assertEqual(escalate.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Strict Lifecycle State Machine & Invalid Transition Enforcement
        # Try invalid jump: Draft -> Completed (Illegal)
        illegal_jump = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "completed", "reason": "Attempting illegal premature completion"},
            format="json",
            **headers,
        )
        self.assertEqual(illegal_jump.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid project lifecycle transition", str(illegal_jump.data))

        # Valid transition: Draft -> Setup
        t_setup = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "setup", "reason": "Contract signed and site camp mobilization approved"},
            format="json",
            **headers,
        )
        self.assertEqual(t_setup.status_code, status.HTTP_200_OK)
        self.assertEqual(t_setup.data["data"]["status"], "setup")

        # Valid transition: Setup -> Active
        t_active = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "active", "reason": "Full earthworks and slipform paver deployed"},
            format="json",
            **headers,
        )
        self.assertEqual(t_active.status_code, status.HTTP_200_OK)
        self.assertEqual(t_active.data["data"]["status"], "active")

        # 5. Create WBS Activities Under Project & Departments
        act1_res = self.client.post(
            f"/api/v1/projects/{project_id}/activities/",
            {
                "code": "ACT-001",
                "name": "Subgrade Stabilization & Geotextile Layer Placement",
                "departmentId": created_depts["GEO-MAR"]["id"],
                "wbsReference": "WBS-1.1.2",
                "locationZone": "CH 12+000 to CH 14+500",
                "status": "planned",
                "progressPercent": 0,
            },
            format="json",
            **headers,
        )
        self.assertEqual(act1_res.status_code, status.HTTP_201_CREATED, act1_res.data)
        act1_id = act1_res.data["data"]["id"]

        act2_res = self.client.post(
            f"/api/v1/projects/{project_id}/activities/",
            {
                "code": "ACT-002",
                "name": "Continuous Reinforced Concrete Pavement (CRCP) Slipform Casting",
                "departmentId": created_depts["HWY-CIV"]["id"],
                "wbsReference": "WBS-2.1.0",
                "locationZone": "Main Carriageway Section 2A",
                "status": "planned",
                "progressPercent": 0,
            },
            format="json",
            **headers,
        )
        self.assertEqual(act2_res.status_code, status.HTTP_201_CREATED, act2_res.data)

        # Duplicate activity code should fail
        dup_act = self.client.post(
            f"/api/v1/projects/{project_id}/activities/",
            {
                "code": "ACT-001",
                "name": "Duplicate activity",
            },
            format="json",
            **headers,
        )
        self.assertEqual(dup_act.status_code, status.HTTP_400_BAD_REQUEST)

        # 6. Active Lifecycle: On Hold & Resume
        # Project encountered weather delay / high tidal surge
        t_hold = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "on_hold", "reason": "Severe Atlantic tidal surge submerged lower berm"},
            format="json",
            **headers,
        )
        self.assertEqual(t_hold.status_code, status.HTTP_200_OK)
        self.assertEqual(t_hold.data["data"]["status"], "on_hold")

        # Resume to active
        t_resume = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "active", "reason": "Tidal waters receded and berm reinforced"},
            format="json",
            **headers,
        )
        self.assertEqual(t_resume.status_code, status.HTTP_200_OK)
        self.assertEqual(t_resume.data["data"]["status"], "active")

        # 7. Update Activity Progress
        act1_patch = self.client.patch(
            f"/api/v1/projects/{project_id}/activities/{act1_id}/",
            {"status": "completed", "progressPercent": 100},
            format="json",
            **headers,
        )
        self.assertEqual(act1_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(act1_patch.data["data"]["status"], "completed")
        self.assertEqual(act1_patch.data["data"]["progressPercent"], 100)

        # 8. Complete Project
        t_complete = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "completed", "reason": "All 10 lanes cast, cured, and core tests verified"},
            format="json",
            **headers,
        )
        self.assertEqual(t_complete.status_code, status.HTTP_200_OK)
        self.assertEqual(t_complete.data["data"]["status"], "completed")

        # 9. Verify Stakeholder Read-Only Boundaries
        self.client.force_authenticate(user=self.stakeholder_highway)
        # Stakeholder can view project, departments, and activities
        stk_view = self.client.get(f"/api/v1/projects/{project_id}/", **headers)
        self.assertEqual(stk_view.status_code, status.HTTP_200_OK)
        self.assertEqual(stk_view.data["data"]["name"], "Lagos-Calabar Coastal Highway — Section 2")

        stk_depts = self.client.get(f"/api/v1/projects/{project_id}/departments/", **headers)
        self.assertEqual(stk_depts.status_code, status.HTTP_200_OK)
        self.assertEqual(len(stk_depts.data["data"]), 4)

        stk_acts = self.client.get(f"/api/v1/projects/{project_id}/activities/", **headers)
        self.assertEqual(stk_acts.status_code, status.HTTP_200_OK)
        self.assertEqual(len(stk_acts.data["data"]), 2)

        # Stakeholder blocked from modifying project or transitioning status
        stk_patch = self.client.patch(
            f"/api/v1/projects/{project_id}/",
            {"name": "Unauthorized Stakeholder Name Edit"},
            format="json",
            **headers,
        )
        self.assertEqual(stk_patch.status_code, status.HTTP_403_FORBIDDEN)

        stk_trans = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "archived"},
            format="json",
            **headers,
        )
        self.assertEqual(stk_trans.status_code, status.HTTP_403_FORBIDDEN)

        stk_dept_create = self.client.post(
            f"/api/v1/projects/{project_id}/departments/",
            {"name": "Unauthorized Dept", "code": "UNAUTH"},
            format="json",
            **headers,
        )
        self.assertEqual(stk_dept_create.status_code, status.HTTP_403_FORBIDDEN)

        # 10. Verify Immutable Activity History & Audit Trail
        hist_res = self.client.get(f"/api/v1/projects/{project_id}/activity-history/", **headers)
        self.assertEqual(hist_res.status_code, status.HTTP_200_OK)
        logs = hist_res.data["data"]
        self.assertGreater(len(logs), 0)
        action_names = [l["action"] for l in logs]
        self.assertIn("projects.created", action_names)
        self.assertIn("projects.department_created", action_names)
        self.assertIn("rbac.project_member_assigned", action_names)
        self.assertIn("projects.status_transitioned", action_names)
        self.assertIn("projects.activity_created", action_names)

    # =========================================================================
    # TEST CASE 2: Eko Atlantic Water Treatment Plant & Supply Facility (EAW-TP-2026)
    # =========================================================================
    def test_case_2_water_treatment_plant_invitation_and_lifecycle(self):
        """
        Complete end-to-end lifecycle of Water Treatment Plant (EAW-TP-2026):
        Org Settings Update -> Member Invitation -> Invitation Acceptance ->
        Project Creation (Setup) -> 3 Departments -> Activities ->
        Active -> Completed -> Archived (Terminal State) ->
        Rejection of transitions from Archived -> Tenant Isolation.
        """
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org_water.id)}

        # 1. Platform Admin updates Organization Profile & Compliance Threshold
        self.client.force_authenticate(user=self.admin_user)
        patch_org = self.client.patch(
            f"/api/v1/organizations/{self.org_water.id}/",
            {
                "complianceThresholdPercent": 95.00,
                "contactEmail": "governance@lswc.ng",
                "cacNumber": "RC-881920",
                "notes": "Primary municipal water infrastructure provider for Lagos State.",
            },
            format="json",
            **headers,
        )
        self.assertEqual(patch_org.status_code, status.HTTP_200_OK)
        self.assertEqual(float(patch_org.data["data"]["compliance_threshold_percent"]), 95.00)

        # 2. Issue Invitation with Pre-assigned Project Code
        inv_res = self.client.post(
            "/api/v1/organizations/invitations/",
            {
                "email": self.pe_water.email,
                "role": SystemRoleSlug.PROJECT_ENGINEER,
                "organizationId": str(self.org_water.id),
                "assignedProjects": ["EAW-TP-2026"],
            },
            format="json",
            **headers,
        )
        self.assertEqual(inv_res.status_code, status.HTTP_201_CREATED, inv_res.data)
        inv_code = inv_res.data["data"]["invitation_code"]

        # 3. Utility Engineer accepts invitation via explicit endpoint
        self.client.force_authenticate(user=self.pe_water)
        accept_res = self.client.post(
            "/api/v1/organizations/invitations/accept/",
            {"invitationCode": inv_code},
            format="json",
            **headers,
        )
        self.assertEqual(accept_res.status_code, status.HTTP_200_OK, accept_res.data)
        self.assertEqual(accept_res.data["data"]["role"], SystemRoleSlug.PROJECT_ENGINEER)

        # Verify replay protection: accepting again fails
        replay_res = self.client.post(
            "/api/v1/organizations/invitations/accept/",
            {"invitationCode": inv_code},
            format="json",
            **headers,
        )
        self.assertEqual(replay_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already been accepted", str(replay_res.data))

        # 4. Create Municipal Water Treatment Project in 'setup' status
        create_res = self.client.post(
            "/api/v1/projects/",
            {
                "name": "Eko Atlantic Water Treatment Plant & Supply Facility",
                "code": "EAW-TP-2026",
                "client": "Lagos State Water Corporation (LSWC)",
                "location": "Eko Atlantic City Coastal District, Victoria Island, Lagos",
                "description": "250 MLD advanced membrane filtration and desalination facility.",
                "status": "setup",
                "leadEngineer": self.pe_water.full_name,
                "lasbcaPermitNumber": "LASBCA/2026/UTL/0887",
                "budgetAmount": "85B",
                "currency": "₦",
                "specsReference": "WHO Potable Water Guidelines & BS EN 12255 Wastewater Treatment",
                "projectType": "Water Treatment & Urban Municipal Utility",
                "priority": "high",
            },
            format="json",
            **headers,
        )
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED, create_res.data)
        project_id = create_res.data["data"]["id"]
        self.assertEqual(create_res.data["data"]["status"], "setup")

        # 5. Create 3 Process Departments
        dept_defs = [
            ("Mechanical, Electrical & Process Engineering", "UTL-MEP", "Pumping skids and MCC panels"),
            ("Reinforced Concrete Reservoir Structures", "STR-CON", "Post-tensioned clear water reservoirs"),
            ("Laboratory Testing & Process Verification", "QA-LAB", "Turbidity, pH, and microbiological testing"),
        ]
        created_depts = {}
        for name, code, desc in dept_defs:
            d_res = self.client.post(
                f"/api/v1/projects/{project_id}/departments/",
                {"name": name, "code": code, "description": desc},
                format="json",
                **headers,
            )
            self.assertEqual(d_res.status_code, status.HTTP_201_CREATED, d_res.data)
            created_depts[code] = d_res.data["data"]

        # 6. Create WBS Activities
        act_defs = [
            ("WBS-01", "Rapid Sand Filtration Basin Concrete Pouring", created_depts["STR-CON"]["id"]),
            ("WBS-02", "High-Pressure Reverse Osmosis Pump Skid Installation", created_depts["UTL-MEP"]["id"]),
            ("WBS-03", "Distribution Piping Hydrostatic Pressure Testing", created_depts["QA-LAB"]["id"]),
        ]
        created_acts = {}
        for code, name, dept_id in act_defs:
            a_res = self.client.post(
                f"/api/v1/projects/{project_id}/activities/",
                {
                    "code": code,
                    "name": name,
                    "departmentId": dept_id,
                    "status": "planned",
                },
                format="json",
                **headers,
            )
            self.assertEqual(a_res.status_code, status.HTTP_201_CREATED, a_res.data)
            created_acts[code] = a_res.data["data"]

        # 7. Progress activities & transition lifecycle: setup -> active -> completed -> archived
        self.client.patch(
            f"/api/v1/projects/{project_id}/activities/{created_acts['WBS-01']['id']}/",
            {"status": "completed", "progressPercent": 100},
            format="json",
            **headers,
        )
        self.client.patch(
            f"/api/v1/projects/{project_id}/activities/{created_acts['WBS-02']['id']}/",
            {"status": "in_progress", "progressPercent": 75},
            format="json",
            **headers,
        )

        # Transition: Setup -> Active
        t_active = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "active", "reason": "Basin cured and pump skids installed"},
            format="json",
            **headers,
        )
        self.assertEqual(t_active.status_code, status.HTTP_200_OK)
        self.assertEqual(t_active.data["data"]["status"], "active")

        # Transition: Active -> Completed
        t_complete = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "completed", "reason": "Plant commissioned and producing 250 MLD potable water"},
            format="json",
            **headers,
        )
        self.assertEqual(t_complete.status_code, status.HTTP_200_OK)
        self.assertEqual(t_complete.data["data"]["status"], "completed")

        # Transition: Completed -> Archived
        t_archive = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "archived", "reason": "Final commissioning documents archived in state repository"},
            format="json",
            **headers,
        )
        self.assertEqual(t_archive.status_code, status.HTTP_200_OK)
        self.assertEqual(t_archive.data["data"]["status"], "archived")

        # 8. Terminal State Enforcement: Archived cannot transition to anything
        t_illegal_resurrect = self.client.post(
            f"/api/v1/projects/{project_id}/transition-status/",
            {"status": "active", "reason": "Attempting to unarchive"},
            format="json",
            **headers,
        )
        self.assertEqual(t_illegal_resurrect.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid project lifecycle transition", str(t_illegal_resurrect.data))

        # 9. Department and Activity Deletion
        del_act = self.client.delete(
            f"/api/v1/projects/{project_id}/activities/{created_acts['WBS-03']['id']}/",
            **headers,
        )
        self.assertEqual(del_act.status_code, status.HTTP_200_OK)
        self.assertTrue(del_act.data["data"]["deleted"])

        del_dept = self.client.delete(
            f"/api/v1/projects/{project_id}/departments/{created_depts['QA-LAB']['id']}/",
            **headers,
        )
        self.assertEqual(del_dept.status_code, status.HTTP_200_OK)
        self.assertTrue(del_dept.data["data"]["deleted"])

        # 10. Multi-Tenant Isolation: Outsider from Isolated Org cannot access EAW-TP-2026
        self.client.force_authenticate(user=self.outsider_user)
        outsider_headers = {"HTTP_X_ORGANIZATION_ID": str(self.org_isolated.id)}
        outsider_access = self.client.get(
            f"/api/v1/projects/{project_id}/",
            **outsider_headers,
        )
        # Should be 404 (or 403) because project is not in their tenant
        self.assertIn(outsider_access.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

        outsider_list = self.client.get("/api/v1/projects/", **outsider_headers)
        self.assertEqual(outsider_list.status_code, status.HTTP_200_OK)
        project_ids_visible = [p["id"] for p in outsider_list.data["data"]]
        self.assertNotIn(project_id, project_ids_visible)
