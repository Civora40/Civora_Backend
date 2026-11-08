# Phase 4 — Organization and Project Management

## 1. Executive Overview & Architecture

Phase 4 establishes Civora's core multi-tenant structural foundation: **Organization Management**, **Project Lifecycle & Configuration**, **Work Breakdown Structure (WBS) & Departments**, and **Project Team Governance**.

Every engineering activity, field submission, quality verification, and compliance report in Civora is anchored to an authorized **Project** owned by an **Organization**. Phase 4 enforces strict server-side tenant boundaries, explicit RBAC role capabilities, state machine lifecycle transitions, and an append-only audit trail.

---

## 2. Domain Data Models

```
┌─────────────────────────────────────────────────────────────┐
│                       Organization                          │
│  - id, name, slug, code, tier, status                       │
│  - compliance_threshold_percent, cac_number, contact_email  │
└──────────────┬──────────────────────────────┬───────────────┘
               │ 1:N                          │ 1:N
               ▼                              ▼
┌──────────────────────────────┐ ┌──────────────────────────────┐
│    OrganizationMembership    │ │    OrganizationInvitation    │
│  - user, role, is_active     │ │  - email, role, code, status │
│  - invited_by                │ │  - assigned_project_codes    │
└──────────────────────────────┘ └──────────────────────────────┘
               │                              │
               │ (owning tenant)              │ (pre-assignment)
               ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│                          Project                            │
│  - id, name, code, status, location, client_name            │
│  - lead_engineer_name, lasbca_permit_number, budget_fmt     │
│  - specs_reference, specificationFolder, progress_percent   │
└──────────────┬──────────────────────────────┬───────────────┘
               │ 1:N                          │ 1:N
               ▼                              ▼
┌──────────────────────────────┐ ┌──────────────────────────────┐
│      ProjectDepartment       │ │      ProjectMembership       │
│  - name, code, description   │ │  - user, role, department    │
│  - project, organization     │ │  - is_active                 │
└──────────────┬───────────────┘ └──────────────────────────────┘
               │ 1:N
               ▼
┌──────────────────────────────┐
│       ProjectActivity        │
│  - code, name, wbs_reference │
│  - status, progress_percent  │
│  - planned/actual dates      │
│  - department, lead_engineer │
└──────────────────────────────┘
```

---

## 3. Strict Project Lifecycle State Machine

Projects cannot undergo arbitrary status changes. Transitions are governed by `ALLOWED_PROJECT_TRANSITIONS` and enforced server-side via `transition_project_status()` and `POST /api/v1/projects/<id>/transition-status/`:

```
 [Draft] ───► [Setup] ───► [Active] ◄───► [On Hold]
    │           │            │               │
    ▼           ▼            ▼               ▼
 [Archived] ◄───┴────────────┴─────────► [Completed] ───► [Archived]
```

### Transition Matrix
| Current Status | Allowed Target Statuses | Description & Business Rules |
| :--- | :--- | :--- |
| **`draft`** | `setup`, `archived` | Initial project creation, specifications definition, scoping. |
| **`setup`** | `active`, `on_hold`, `archived` | Camp mobilization, contractor site access, team onboarding. |
| **`active`** | `on_hold`, `completed` | Active field construction, daily submissions, task execution. |
| **`on_hold`** | `active`, `archived` | Temporary halt (weather delay, site safety review, funding pause). |
| **`completed`** | `archived` | Construction finished, commissioning complete, all reports released. |
| **`archived`** | *(None — Terminal State)* | Immutable historical project record; cannot transition back to active. |

---

## 4. REST API Endpoint Catalog

### 4.1 Organizations & Invitations (`apps/organizations`)

| HTTP Method | Route | Description | Capability Required |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/organizations/` | List active user's organizations (or all for Platform Admin) | `IsAuthenticated` |
| `POST` | `/api/v1/organizations/` | Provision new tenant organization | `platform_admin` |
| `GET` | `/api/v1/organizations/<id>/` | Get organization details and compliance settings | Tenant Member |
| `PATCH` | `/api/v1/organizations/<id>/` | Update organization profile & compliance threshold | `organizations.manage` |
| `GET` | `/api/v1/organizations/invitations/` | List pending and issued invitations | `organizations.invite_member` |
| `POST` | `/api/v1/organizations/invitations/` | Issue organization invitation with role & pre-assigned projects | `organizations.invite_member` |
| `POST` | `/api/v1/organizations/invitations/accept/` | Accept invitation code (`CIV-INV-...`) with replay prevention | `IsAuthenticated` |
| `POST` | `/api/v1/organizations/invitations/<id>/revoke/` | Revoke a pending invitation | `organizations.invite_member` |
| `GET` | `/api/v1/organizations/memberships/` | List tenant user memberships | Tenant Member |
| `POST` | `/api/v1/organizations/memberships/` | Assign user to organization with explicit role | `organizations.invite_member` |
| `PATCH` | `/api/v1/organizations/memberships/<id>/` | Update user organization role | `organizations.invite_member` |
| `DELETE` | `/api/v1/organizations/memberships/<id>/` | Revoke user organization membership | `organizations.invite_member` |

### 4.2 Projects, Departments & WBS Activities (`apps/projects`)

| HTTP Method | Route | Description | Capability Required |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/projects/` | List authorized projects for the active user | `projects.view` / Tenant Member |
| `POST` | `/api/v1/projects/` | Create a new project & generate specification folder | `projects.create` |
| `GET` | `/api/v1/projects/<id>/` | Get project detail, departments & statistics | `projects.view` |
| `PATCH` | `/api/v1/projects/<id>/` | Update project configuration, dates, specs, or status | `projects.update` |
| `POST` | `/api/v1/projects/<id>/transition-status/` | Explicit lifecycle state machine transition with reason | `projects.update` |
| `GET` | `/api/v1/projects/<id>/members/` | List project team members | `projects.view` |
| `POST` | `/api/v1/projects/<id>/members/` | Assign member with role & optional department | `projects.manage_members` |
| `PATCH` | `/api/v1/projects/<id>/members/<id>/` | Update member role or department assignment | `projects.manage_members` |
| `DELETE` | `/api/v1/projects/<id>/members/<id>/` | Remove member from project | `projects.manage_members` |
| `GET` | `/api/v1/projects/<id>/departments/` | List project departments / disciplines | `projects.view` |
| `POST` | `/api/v1/projects/<id>/departments/` | Create project department (unique code per project) | `projects.update` |
| `GET` | `/api/v1/projects/<id>/departments/<id>/` | Get department detail | `projects.view` |
| `PATCH` | `/api/v1/projects/<id>/departments/<id>/` | Update department attributes | `projects.update` |
| `DELETE` | `/api/v1/projects/<id>/departments/<id>/` | Delete department | `projects.update` |
| `GET` | `/api/v1/projects/<id>/activities/` | List WBS activities (supports `?status=` and `?department_id=`) | `projects.view` |
| `POST` | `/api/v1/projects/<id>/activities/` | Create WBS activity | `projects.update` / `tasks.manage` |
| `GET` | `/api/v1/projects/<id>/activities/<id>/` | Get activity details and progress | `projects.view` |
| `PATCH` | `/api/v1/projects/<id>/activities/<id>/` | Update activity progress, dates, or status | `projects.update` / `tasks.execute` |
| `DELETE` | `/api/v1/projects/<id>/activities/<id>/` | Delete WBS activity | `projects.update` / `tasks.manage` |
| `GET` | `/api/v1/projects/<id>/activity-history/` | Chronological audit trail & timeline for the project | `projects.view` |

---

## 5. End-to-End Implementation: Two Project Test Cases

### Test Case 1: Lagos-Calabar Coastal Highway — Section 2 (`LCR-S2-2026`)

- **Domain**: Road, Highway & Coastal Defense Infrastructure
- **Tenant**: Federal Ministry of Works & Coastal Infrastructure (`CIV-FMWH-01`)
- **Client**: Federal Ministry of Works & Housing (FMWH)
- **Budget**: `₦420B`
- **Permit**: `LASBCA/2026/HWY/0142`
- **Specification Standard**: Eurocode 2 & FMWH Standard Specifications (C35/45 Pavement Grade)
- **4 Engineering Departments Provisioned**:
  1. `HWY-CIV` — Highway & Concrete Pavement Engineering
  2. `GEO-MAR` — Coastal Geotechnical & Marine Reclamation
  3. `QA-MAT` — Quality Assurance & Material Testing
  4. `HSE-ENV` — Health, Safety & Coastal Environmental Protection
- **Team Assignment**:
  - `lead.highway@civora.ng` (`project_engineer`, creator, bound to `HWY-CIV`)
  - `reviewer.coren@civora.ng` (`supervising_reviewer`, bound to `QA-MAT`)
  - `oversight.ministry@fmwh.gov.ng` (`read_only_stakeholder`)
- **WBS Activities Tracked**:
  - `ACT-001`: Subgrade Stabilization & Geotextile Layer Placement (`GEO-MAR`) — `100% completed`
  - `ACT-002`: Continuous Reinforced Concrete Pavement (CRCP) Slipform Casting (`HWY-CIV`)
- **Lifecycle Executed**:
  - Initial `draft` -> Invalid transition `draft` -> `completed` blocked (`400 Bad Request`)
  - Valid `draft` -> `setup` (camp mobilization)
  - Valid `setup` -> `active` (earthworks deployed)
  - Encountered coastal surge: `active` -> `on_hold`
  - Resumed after mitigation: `on_hold` -> `active`
  - Construction finished & verified: `active` -> `completed`
- **Stakeholder Boundary Enforcement**:
  - Stakeholder views project, departments, and activities (`200 OK`)
  - Stakeholder blocked from editing project name (`403 Forbidden`)
  - Stakeholder blocked from transitioning status (`403 Forbidden`)
  - Stakeholder blocked from creating departments (`403 Forbidden`)
- **Audit Verification**:
  - `/api/v1/projects/<id>/activity-history/` captures `projects.created`, `projects.department_created`, `rbac.project_member_assigned`, `projects.status_transitioned`, and `projects.activity_created`.

---

### Test Case 2: Eko Atlantic Water Treatment Plant & Supply Facility (`EAW-TP-2026`)

- **Domain**: Water Treatment, Civil Hydraulic & Urban Municipal Utilities
- **Tenant**: Lagos State Water Corporation (`CIV-LSWC-02`)
- **Client**: Lagos State Water Corporation (LSWC)
- **Budget**: `₦85B`
- **Permit**: `LASBCA/2026/UTL/0887`
- **Specification Standard**: WHO Potable Water Guidelines & BS EN 12255 Wastewater Treatment
- **Organization Profile & Governance**:
  - Compliance threshold updated to `95.00%`
  - CAC number `RC-881920` and governance contact configured
- **Invitation Flow**:
  - Issued invitation `CIV-INV-EAW-01` with pre-assigned project `EAW-TP-2026`
  - Accepted via `/api/v1/organizations/invitations/accept/`
  - Verified replay protection (re-accepting rejected with `400 Bad Request`)
- **3 Process Departments Provisioned**:
  1. `UTL-MEP` — Mechanical, Electrical & Process Engineering
  2. `STR-CON` — Reinforced Concrete Reservoir Structures
  3. `QA-LAB` — Laboratory Testing & Process Verification
- **WBS Activities Tracked**:
  - `WBS-01`: Rapid Sand Filtration Basin Concrete Pouring (`STR-CON`) — `100% completed`
  - `WBS-02`: High-Pressure Reverse Osmosis Pump Skid Installation (`UTL-MEP`) — `75% in_progress`
  - `WBS-03`: Distribution Piping Hydrostatic Pressure Testing (`QA-LAB`) — `deleted`
- **Lifecycle to Terminal State**:
  - Initial `setup` -> `active` -> `completed` -> `archived`
  - Terminal state validation: Attempting to resurrect from `archived` -> `active` rejected (`400 Bad Request`)
- **Multi-Tenant Isolation**:
  - Outsider from isolated tenant (`CIV-ISO-99`) querying `EAW-TP-2026` returns `404 Not Found`
  - Project does not appear in outsider's project list.

---

## 6. Verification and Automated Test Coverage

The test suite in [`tests/security/test_phase4_organization_and_project_management.py`](file:///Users/mac/Desktop/Civora_Backend/unifed_civora_backend/tests/security/test_phase4_organization_and_project_management.py) executes both project test cases end-to-end.

```bash
/Users/mac/Desktop/orr_backend/.venv/bin/python3 manage.py test tests.security --settings=config.settings.test
```

**Results:**
- `Found 19 test(s)`
- `Ran 19 tests in 2.423s`
- **`OK` (19/19 Passing)**
