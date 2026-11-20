# Phase 5 — Field Submission and Evidence Management

## 1. Executive Overview & Architecture

Phase 5 establishes Civora's central field intelligence pipeline: **Field Submission Intake**, **Evidence Media Management**, **Cryptographic Integrity Verification**, and **Engineering Review Governance**.

Every site observation, concrete test break, QA/QC checklist, drone flyover, and contractor waybill enters Civora through this subsystem. The system guarantees that:
1. **Raw field evidence is immutable**: Original contractor and field engineer payloads are preserved without alteration. Engineering annotations, AI compliance assessments, and status transitions are recorded separately.
2. **Timestamps are disentangled**: The original field event timestamp (`submitted_at`) is captured independently from the server intake timestamp (`created_at`), allowing accurate lag tracking and offline synchronization.
3. **Evidence is cryptographically signed**: Every file attachment is hashed with SHA-256 upon intake, creating a tamper-evident audit record suitable for regulatory inspections (COREN, LASBCA, FMWH).
4. **Access is private and tenant-bounded**: Evidence media is stored under secure, private storage keys and accessible only via authenticated, scoped streaming or time-limited download endpoints.

---

## 2. Domain Data Models & Entity Relationships

```
┌─────────────────────────────────────────────────────────────┐
│                          Project                            │
│  - id, code, name, organization, approval_status            │
└──────────────┬──────────────────────────────┬───────────────┘
               │ 1:N                          │ 1:N
               ▼                              ▼
┌──────────────────────────────┐ ┌──────────────────────────────┐
│      ProjectDepartment       │ │       ProjectActivity        │
│  - id, code, name            │ │  - id, code, name            │
└──────────────┬───────────────┘ └──────────────┬───────────────┘
               │ (optional)                   │ (optional)
               ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        Submission                           │
│  - id, reference_code, organization, project                 │
│  - department, activity, submitted_by, submitter_name        │
│  - submitter_identifier (phone/WhatsApp/Telegram/email)     │
│  - channel (mobile_app, web_portal, whatsapp, telegram, api) │
│  - submission_type (daily_progress, quality_inspection, ...) │
│  - status (received, validating, pending_review, approved...)│
│  - title, raw_text, structured_payload                       │
│  - gps_latitude, gps_longitude, location_label               │
│  - weather_condition, completeness_score, is_late           │
│  - submitted_at, reviewed_by, reviewed_at, review_notes      │
└──────────────┬──────────────────────────────┬───────────────┘
               │ 1:N                          │ 1:N
               ▼                              ▼
┌──────────────────────────────┐ ┌──────────────────────────────┐
│   SubmissionStatusHistory    │ │     SubmissionAttachment     │
│  - id, submission, project   │ │  - id, submission, project   │
│  - from_status, to_status    │ │  - file_name, storage_key    │
│  - changed_by, reason        │ │  - mime_type, media_type     │
│  - metadata, created_at      │ │  - file_size_bytes           │
└──────────────────────────────┘ │  - sha256_checksum           │
                                 │  - caption, captured_at      │
                                 │  - gps_latitude, gps_long    │
                                 │  - is_verified, exif_meta    │
                                 └──────────────┬───────────────┘
                                                │ (evidence linking)
                                                ▼
                                 ┌──────────────────────────────┐
                                 │    TaskCompletionEvidence    │
                                 │  - task, submission          │
                                 │  - attachment, submitted_by  │
                                 │  - is_accepted, reviewed_by  │
                                 └──────────────────────────────┘
```

---

## 3. Submission Lifecycle State Machine

Field reports follow an explicit state machine with role validation and immutable history logging:

```
                  ┌───────────────┐
                  │   RECEIVED    │
                  └───────┬───────┘
                          │
                          ▼
                  ┌───────────────┐
         ┌───────►│  VALIDATING   │────────┐
         │        └───────┬───────┘        │
         │                │                │
         │                ▼                │
         │        ┌───────────────┐        │
         ├───────►│PENDING_REVIEW │◄───┐   │
         │        └───┬───┬───┬───┘    │   │
         │            │   │   │        │   │
         │ ┌──────────┘   │   └────┐   │   │
         │ │              ▼        │   │   │
         │ │    ┌─────────────┐    │   │   │
         │ │    │   NEEDS_    │────┘   │   │
         │ │    │CLARIFICATION│────────┘   │
         │ │    └─────────────┘            │
         │ ▼                               ▼
   ┌───────────┐                     ┌───────────┐
   │ APPROVED  │                     │ REJECTED  │
   └─────┬─────┘                     └─────┬─────┘
         │                                 │
         ▼                                 ▼
   ┌───────────┐                     ┌───────────┐
   │ ARCHIVED  │                     │ ARCHIVED  │
   └───────────┘                     └───────────┘
```

### Transition Matrix
| Current Status | Allowed Target Statuses | Responsible Roles | Description & Validation |
| :--- | :--- | :--- | :--- |
| **`received`** | `validating`, `pending_review`, `rejected` | System / Field Personnel | Initial arrival from WhatsApp, Telegram, Mobile App, or API. |
| **`validating`** | `pending_review`, `needs_clarification`, `rejected` | Ingestion Worker / PE | System validates GPS geofence, media checksums, and form completeness. |
| **`pending_review`**| `approved`, `needs_clarification`, `rejected` | Project Engineer / Admin | Technical review against project engineering specs and WBS milestones. |
| **`needs_clarification`** | `pending_review`, `validating`, `rejected` | Submitter / PE | Clarification requested (RFI); waits for updated info or resubmission. |
| **`approved`** | `archived` | Project Engineer / Admin | Field evidence verified, signed off, and eligible for project report inclusion. |
| **`rejected`** | `pending_review`, `archived` | Project Engineer / Admin | Non-compliant submission rejected (can be reopened for appeal or archived). |
| **`archived`** | *(None — Terminal State)* | Platform Admin | Historical preservation; locked from further modifications. |

---

## 4. Evidence Integrity, Cryptographic Hashing & Private Storage

### 4.1 SHA-256 Checksum Calculation
When any file (photo, CAD drawing, PDF break certificate, voice note) is received:
- The backend streams the binary payload into a SHA-256 hash accumulator.
- The resulting 64-character hex digest is recorded in `SubmissionAttachment.sha256_checksum`.
- Any subsequent download or audit comparison recalculates the hash to guarantee zero bit rot or tampering.

### 4.2 Strict File Validation & Defense-in-Depth
- **Size Limitation**: Standard submissions cap individual attachments at 50MB.
- **Content Type Whitelist**: Images (`image/jpeg`, `image/png`, `image/webp`), Documents (`application/pdf`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/msword`), CAD (`image/vnd.dwg`, `application/octet-stream`).
- **Executable Blocking**: File extensions such as `.exe`, `.bat`, `.cmd`, `.sh`, `.php`, `.py`, `.js` are strictly rejected.

### 4.3 Private Object Key Structure
Attachments are stored using tenant-isolated path hierarchies:
```
evidence/<organization_code>/<project_code>/<submission_ref>/<timestamp>_<clean_filename>
```
Direct public access to storage is blocked. Media retrieval is gated by:
1. Active session authentication (`IsAuthenticated` / Bearer token).
2. Tenant membership and project authorization (`get_authorized_projects_for_user`).
3. Required capability (`submissions.view`).
4. Streaming via `GET /api/v1/evidence/<id>/download/` with proper `Content-Disposition`, `Content-Type`, and security headers (`X-Content-Type-Options: nosniff`).

---

## 5. REST API Endpoint Catalog

### 5.1 Submissions Endpoints (`apps/submissions`)

| HTTP Method | Route | Description | Capability Required |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/submissions/` | List & filter submissions (`?projectId=`, `?departmentId=`, `?status=`, `?channel=`, `?search=`) | `submissions.view` |
| `POST` | `/api/v1/submissions/` | Create a new manual field submission with structured payload and attachments | `submissions.create` |
| `GET` | `/api/v1/submissions/<id>/` | Retrieve complete submission details, attachments, history, and linked tasks | `submissions.view` |
| `PATCH` | `/api/v1/submissions/<id>/` | Update submission, review notes, status, linked task, or add an RFI query | `submissions.verify` |
| `POST` | `/api/v1/submissions/<id>/transition-status/` | Explicit state machine transition with reason & status history log | `submissions.verify` |
| `POST` | `/api/v1/submissions/<id>/link-task/` | Link submission to a specific WBS Project Task (`TaskCompletionEvidence`) | `submissions.verify` |
| `POST` | `/api/v1/submissions/<id>/request-info/` | Issue an RFI / clarification request to the field submitter | `submissions.verify` |
| `POST` | `/api/v1/submissions/<id>/attachments/` | Upload and attach files directly to an existing submission | `submissions.create` |

### 5.2 Evidence Endpoints (`apps/evidence`)

| HTTP Method | Route | Description | Capability Required |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/evidence/` | List evidence attachments across authorized projects (`?projectId=`, `?mediaType=`, `?isVerified=`) | `submissions.view` |
| `POST` | `/api/v1/evidence/upload/` | Multipart file upload with automatic SHA-256 calculation and metadata extraction | `submissions.create` |
| `GET` | `/api/v1/evidence/<id>/` | Retrieve attachment metadata, checksum, and verification status | `submissions.view` |
| `GET` | `/api/v1/evidence/<id>/download/` | Authenticated file stream/download with tenant & project security checks | `submissions.view` |
| `POST` | `/api/v1/evidence/<id>/verify/` | Verify/certify evidence attachment by an authorized Project Engineer | `submissions.verify` |
| `DELETE`| `/api/v1/evidence/<id>/` | Delete unverified attachment before report inclusion | `submissions.verify` |

---

## 6. End-to-End Implementation: Two Project Test Cases

### Test Case 1: Lagos-Calabar Coastal Highway — Section 2 (`LCR-S2-2026`)

- **Domain**: Road, Highway & Coastal Defense Infrastructure
- **Tenant**: Federal Ministry of Works & Coastal Infrastructure (`CIV-FMWH-01`)
- **Project**: `LCR-S2-2026` (Approved & Active)
- **Field Scenario**:
  - Continuous Reinforced Concrete Pavement (CRCP) slipform casting at Chainage KM 42+500.
  - Submitter: `engr.babajide@fmwh.gov.ng` (Field Civil Engineer, WhatsApp channel).
- **Submission Record**:
  - Reference: `SUB-LCR-S2-2026-0042`
  - Department: `HWY-CIV` (Highway & Concrete Pavement Engineering)
  - Activity: `ACT-002` (CRCP Slipform Casting)
  - Telemetry: GPS `6.428100, 3.421900`, Weather: "Humid / 29°C overcast".
  - Structured Data: C35/45 mix ratio, 75mm slump, 12 cube samples cast.
- **Evidence Dossier**:
  1. `crcp_pour_km42_500.jpg` (Site Photo, `image/jpeg`, SHA-256 computed).
  2. `concrete_cylinder_break_certificate_28day.pdf` (Lab Break Certificate, `application/pdf`, 38.4 MPa achieved).
- **Engineering Workflow Executed**:
  1. Intake in `received` status -> Initial transition to `pending_review`.
  2. Project Engineer issues RFI (`needs_clarification`): "Confirm curing water temperature during 7-day initial submersion."
  3. Submitter responds via WhatsApp with curing tank temperature logs (`23.5°C`).
  4. Project Engineer transitions status: `pending_review` -> `approved` with sign-off notes: "Meets Eurocode 2 C35/45 specification."
  5. Project Engineer links submission to Task `TSK-LCR-001` via `TaskCompletionEvidence`.
  6. QA Engineer certifies PDF lab certificate: `POST /api/v1/evidence/<id>/verify/` -> `is_verified=True`.
- **Security & Authorization Validations**:
  - Read-Only Stakeholder (`oversight.ministry@fmwh.gov.ng`) views submission and downloads evidence (`200 OK`).
  - Read-Only Stakeholder blocked from transitioning status to `approved` (`403 Forbidden`).
  - Read-Only Stakeholder blocked from uploading evidence files (`403 Forbidden`).

---

### Test Case 2: Eko Atlantic Water Treatment Plant (`EAW-TP-2026`)

- **Domain**: Water Treatment, Civil Hydraulic & Urban Municipal Utilities
- **Tenant**: Lagos State Water Corporation (`CIV-LSWC-02`)
- **Project**: `EAW-TP-2026` (Approved & Active)
- **Field Scenario**:
  - High-Pressure Reverse Osmosis (RO) pump skid hydrostatic pressure testing and raw water turbidity analysis.
  - Submitter: Ingested via direct API from site instrumentation logger (`API_INGEST`).
- **Submission Record**:
  - Reference: `SUB-EAW-TP-2026-RO-01`
  - Department: `UTL-MEP` (Mechanical, Electrical & Process Engineering)
  - Activity: `WBS-02` (High-Pressure RO Pump Skid Installation)
  - Telemetry: GPS `6.415000, 3.411000`, Location: "RO Hall Basement Skid 3".
- **Evidence Attachments**:
  - `ro_skid3_hydrostatic_pressure_log.pdf` (Hydrostatic test log, 16 bar sustained for 4 hours with 0 pressure drop).
  - `turbidity_calibration_readout.png` (Instrument readout, 0.12 NTU compliance).
- **Multi-Parameter Search & Filtering Validations**:
  - Filtering by `?projectId=EAW-TP-2026` returns exactly the Eko Atlantic submissions.
  - Filtering by `?departmentId=<UTL-MEP>` isolates mechanical/process records.
  - Filtering by `?status=pending_review` isolates submissions requiring engineer action.
  - Filtering by `?channel=api_ingest` returns API-ingested telemetry.
  - Full-text search for `hydrostatic` finds the specific test record.
- **Multi-Tenant Boundary Isolation**:
  - User from isolated tenant (`CIV-FMWH-01`) attempting to access Eko Atlantic submission or download its evidence attachments receives `404 Not Found`.
  - Storage keys are strictly partitioned; no file leakage across tenants.

---

## 7. Verification and Automated Test Coverage

The test suite in [`tests/security/test_phase5_field_submission_and_evidence_management.py`](file:///Users/mac/Desktop/Civora_Backend/unifed_civora_backend/tests/security/test_phase5_field_submission_and_evidence_management.py) verifies the entire lifecycle and security model end-to-end:

1. Manual submission creation with structured payload and multipart file uploads.
2. Server-side SHA-256 cryptographic checksum calculation and storage key verification.
3. Strict submission lifecycle state machine transitions with validation error handling.
4. RFI / query text dispatch and status escalation to `needs_clarification`.
5. Linking submissions and attachments to WBS tasks via `TaskCompletionEvidence`.
6. Evidence verification sign-off (`is_verified` toggle).
7. Authenticated evidence streaming/download with security headers and permission checks.
8. Query parameter filtering (`projectId`, `departmentId`, `status`, `channel`, `search`).
9. Read-only stakeholder permission enforcement (can view/download, cannot modify/verify/upload).
```bash
cd /Users/mac/Desktop/Civora_Backend/unifed_civora_backend && /Users/mac/Desktop/orr_backend/.venv/bin/python3 manage.py test tests.security --settings=config.settings.test
```

**Results:**
- `Found 23 test(s)`
- `Ran 23 tests in 2.195s`
- **`OK` (23/23 Passing)**

