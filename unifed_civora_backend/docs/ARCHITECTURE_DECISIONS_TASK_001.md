# Task 001 — Architecture Assessment, Decisions & Unresolved Requirements

**Repository:** `Civora_Backend/unifed_civora_backend`  
**Phase Scope:** Phase 1 (Backend Foundation & Project Setup) & Phase 2/Task 001 (Multi-Tenant Database Foundation & RBAC Boundaries)  
**Date:** 30 October 2026

---

## 1. Repository Architecture Assessment

1. **Initial State:** The `/Users/mac/Desktop/Civora_Backend` workspace was a clean repository containing the technical implementation roadmap (`CIVORA_BACKEND_IMPLEMENTATION_ROADMAP.md` / `README.md`) and its branded PDF specification.
2. **Ecosystem Alignment:** Civora operates across four repositories:
   - `Civora_Backend` (`unifed_civora_backend`) — Standalone Python/Django centralized API and processing hub
   - `Civora-PE_and_Stakeholder_Frontend` — Next.js web application for Project Engineers and Read-Only Stakeholders
   - `Civora-Admin_Frontend` — Platform and Organization Administration portal
   - `Civora_Mobile_App` — Field operations mobile application
3. **Established Architecture (`unifed_civora_backend/`):**
   - **`config/`**: Multi-environment settings (`env.py`, `base.py`, `development.py`, `staging.py`, `production.py`, `test.py`), Celery queue configuration (`celery.py`), and versioned `/api/v1/` URL dispatcher (`urls.py`).
   - **`common/`**: Abstract UUID, timestamped, tenant-scoped (`TenantScopedModel`), and project-scoped (`ProjectScopedModel`) models; server-side `TenantContextMiddleware`; standardized JSON response envelopes; unified DRF exception handler; cursor/page pagination; and repeatable development seeding (`python manage.py seed_dev`).
   - **`apps/`**: 15 domain-isolated Django applications following the `models.py` / `selectors.py` / `services.py` / `serializers.py` / `views.py` pattern.
   - **`infrastructure/`**: Provider-independent adapters for private object storage (`storage/`), transactional email (`email/`), LLM providers (`ai_provider/`), and structured JSON logging (`logging/`).

---

## 2. Key Architectural Decisions

1. **Database-Enforced Multi-Tenancy (`TenantScopedModel` & `ProjectScopedModel`):**
   - Every tenant-owned record carries an explicit non-nullable `organization` foreign key with composite database indexes.
   - Every project-owned record carries both `organization` and `project` foreign keys and validates in `clean()` / `save()` that `project.organization_id == self.organization_id`, preventing cross-tenant foreign-key injection.
2. **Zero Trust on Client-Supplied Tenant Headers (`TenantContextMiddleware`):**
   - While clients may pass `X-Organization-ID` to select an active workspace, the middleware resolves it strictly against `user.organization_memberships.filter(is_active=True)`. Spoofed organization IDs are ignored and rejected by `IsTenantMember` with `403 Forbidden`.
3. **Explicit Capability-Based RBAC (`access_control`):**
   - Permissions are modeled as atomic capabilities (e.g., `submissions.verify`, `reports.draft`, `reports.approve`, `reports.release`) rather than checking role names in views.
   - Separation of duties is enforced out of the box: `project_engineer` holds `submissions.verify` and `reports.draft`, while `reports.approve` and `reports.release` are restricted to `supervising_reviewer` / authorized governance roles.
4. **Append-Only Audit Trail (`apps.audit.models.AuditLog`):**
   - `AuditLog.save()` and `AuditLog.delete()` raise `PermissionDenied` on any attempt to modify or delete an existing record.

---

## 3. Unresolved Product & Technical Questions (To Confirm Before Phases 6–10)

1. **WhatsApp Group Ingestion Capability (Phase 6):**
   - Confirm via proof-of-concept whether the target WhatsApp Business Cloud API tier supports the proposed 9-group ingestion workflow or requires individual/bot-tagged channel routing.
2. **Field Performance Scoring Formula & Exemptions (Phase 7):**
   - The 80% default compliance threshold is stored as a configurable field (`Organization.compliance_threshold_percent`). Confirm the exact denominator (assigned tasks vs. scheduled daily check-ins), grace periods for late submissions, and who has authority to approve score exemptions.
3. **Report Approval Separation-of-Duties Matrix (Phase 10):**
   - Confirm whether an `organization_admin` can release a report they authored, or if final release strictly requires a distinct `supervising_reviewer` user account.
4. **Stakeholder Report Visibility Granularity (Phase 11):**
   - Confirm whether `read_only_stakeholder` users assigned to a project automatically see all `Released` reports for that project, or if reports require per-report stakeholder distribution lists.

