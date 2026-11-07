# Phase 3 — Authentication and Authorization (RBAC) Architecture

This document defines the authentication, session management, multi-tenant isolation, role-based access control (RBAC), separation-of-duties, and security audit trail implementation for the Civora Unified Backend (`unifed_civora_backend`).

---

## 1. Authentication & Session Lifecycle

### 1.1 Endpoints (`/api/v1/auth/`)
| Endpoint | Method | Access | Purpose |
| :--- | :--- | :--- | :--- |
| `/api/v1/auth/register/` | `POST` | Public | Controlled user registration with invitation validation, anti-escalation guard, initial `Account` & `Session` creation, and signed email verification token issuance. |
| `/api/v1/auth/login/` | `POST` | Public | Standard email/password authentication issuing JWT `access` & `refresh` tokens and tracking a `Session` record by `jti`. |
| `/api/v1/auth/admin/login/` | `POST` | Public | Dedicated Platform Administrator sign-in enforcing `is_platform_admin=True` (`403 Forbidden` for non-admin accounts). |
| `/api/v1/auth/logout/` | `POST` | Authenticated / Token | Terminates the active `Session` (`is_revoked=True`) and logs `auth.logout` & `auth.session_revoked`. |
| `/api/v1/auth/sessions/` | `GET` | Authenticated | Lists tracked sessions (`jti`, `client_surface`, `ip_address`, `user_agent`, `is_revoked`, `expires_at`) for the current user (or all users for Platform Admins). |
| `/api/v1/auth/sessions/<session_id>/revoke/` | `POST`, `DELETE` | Authenticated | Explicitly revokes a tracked session (`is_revoked=True`), immediately invalidating subsequent API requests using that session's JWT. |
| `/api/v1/auth/verify-email/request/` | `POST` | Public / Auth | Issues a time-bound cryptographically signed email verification token (`TimestampSigner`). |
| `/api/v1/auth/verify-email/confirm/` | `POST` | Public | Validates the signed token, marks `User.is_email_verified=True` and `Account.is_verified=True`, and records `auth.email_verified`. |
| `/api/v1/auth/password-reset/` | `POST` | Public | Issues a one-time signed password recovery token stored as a SHA-256 hash on `Account.metadata`. |
| `/api/v1/auth/password-reset/confirm/` | `POST` | Public | Validates the one-time token, updates the password, invalidates the token hash against replay, revokes all active `Session` records for the user, and records `auth.password_reset_completed`. |
| `/api/v1/auth/refresh/` | `POST` | Public | Rotates JWT refresh/access tokens. |

### 1.2 Request-Level Session & Suspension Enforcement (`CivoraJWTAuthentication`)
Every authenticated API request is validated by `apps.authentication.backends.CivoraJWTAuthentication`:
1. Rejects requests if `not user.is_active` or `user.account_status == "suspended"`.
2. Extracts the JWT `jti` claim and checks `civora_auth_sessions` (`Session`). If the session is marked `is_revoked=True` or `expires_at <= now`, the request is rejected with `401 Unauthorized`.

---

## 2. Canonical Role & Explicit Capability Matrix

Permissions are modeled as explicit capability atoms (`apps.access_control.models.Permission`) rather than relying on role names alone:

| Capability Codename | Module | `platform_admin` | `organization_admin` | `project_engineer` | `supervising_reviewer` | `field_personnel` | `read_only_stakeholder` |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `organizations.manage` | `organizations` | ✅ | ✅ | — | — | — | — |
| `organizations.invite_member` | `organizations` | ✅ | ✅ | — | — | — | — |
| `projects.create` | `projects` | ✅ | ✅ | — | — | — | — |
| `projects.update` | `projects` | ✅ | ✅ | ✅ | — | — | — |
| `projects.view` | `projects` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `projects.manage_members` | `projects` | ✅ | ✅ | ✅ | — | — | — |
| `submissions.create` | `submissions` | ✅ | ✅ | ✅ | — | ✅ | — |
| `submissions.view` | `submissions` | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `submissions.verify` | `submissions` | ✅ | ✅ | ✅ | — | — | — |
| `tasks.manage` | `tasks` | ✅ | ✅ | ✅ | — | — | — |
| `tasks.execute` | `tasks` | ✅ | ✅ | ✅ | — | ✅ | — |
| `reports.draft` | `reports` | ✅ | ✅ | ✅ | — | — | — |
| `reports.review` | `approvals` | ✅ | ✅ | — | ✅ | — | — |
| `reports.approve` | `approvals` | ✅ | ✅ | — | ✅ | — | — |
| `reports.release` | `approvals` | ✅ | ✅ | — | ✅ | — | — |
| `reports.view_released` | `reports` | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| `audit.view` | `audit` | ✅ | ✅ | — | ✅ | — | — |

---

## 3. Separation of Duties & Governance Rules

1. **No Self-Assigned Privileged Roles**:
   - Registration (`POST /api/v1/auth/register/`) never grants `platform_admin` or `organization_admin`, even if supplied in the payload.
   - Non-platform-admins cannot assign `platform_admin` on organization memberships, project memberships, or invitations (`rbac.privilege_escalation_blocked`).
2. **Read-Only Stakeholder Boundaries**:
   - `read_only_stakeholder` accounts can view authorized project summaries (`projects.view`) and approved/released reports (`reports.view_released`).
   - They cannot view raw field submissions (`submissions.view`), create/verify submissions, create/edit tasks, draft reports (`reports.draft`), view unreleased draft/in-review reports, or sign off engineering approvals (`reports.approve`).
3. **Report Author vs. Approver Separation of Duties**:
   - Enforced by `enforce_report_approval_separation_of_duties` in `apps/access_control/services.py`:
     - Final report approval (`reports.approve`) and stakeholder release (`reports.release`) require explicit reviewer/admin capability.
     - A `project_engineer` (or report author without tenant admin override) cannot approve or release their own authored final report (`rbac.separation_of_duties_blocked`).

---

## 4. Audited Security & Permission Events (`civora_audit_logs`)

All sensitive authentication and authorization events are recorded in the append-only `AuditLog` table:
- `auth.user_registered`, `auth.session_authenticated`, `auth.login_failed`, `auth.login_denied_suspended`, `auth.admin_login_denied`, `auth.logout`, `auth.session_revoked`
- `auth.email_verification_requested`, `auth.email_verified`
- `auth.password_reset_requested`, `auth.password_reset_completed`
- `rbac.organization_role_assigned`, `rbac.organization_role_changed`, `MEMBERSHIP_REVOKED`, `INVITATION_ISSUED`, `INVITATION_REVOKED`
- `rbac.project_member_assigned`, `rbac.project_role_changed`, `rbac.project_member_removed`
- `rbac.permission_denied`, `rbac.tenant_access_denied`, `rbac.privilege_escalation_blocked`, `rbac.separation_of_duties_blocked`
