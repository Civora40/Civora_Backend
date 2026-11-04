# Phase 2 — Database Architecture and Data Modelling

## 1. Architectural Principles & Multi-Tenant Boundaries

Every record in the Civora backend enforces explicit multi-tenant isolation through abstract base models defined in [`common/models.py`](../common/models.py):

- **`UUIDModel`**: UUIDv4 primary key (`id`) across all entities to prevent sequential ID enumeration.
- **`TimestampedModel`**: Standardized UTC `created_at` (indexed) and `updated_at` timestamps.
- **`TenantScopedModel`**: Requires an explicit `organization` foreign key (`organizations.Organization`) and exposes `TenantScopedQuerySet.for_organization(organization_id)`.
- **`ProjectScopedModel`**: Requires both `organization` and `project` (`projects.Project`) foreign keys, automatically validates in `clean()` and `save()` that `project.organization_id == self.organization_id`, and exposes `TenantScopedQuerySet.for_project(organization_id, project_id)`.

---

## 2. Core Domain Entities & Relationships

### 2.1 Identity (`apps/users` & `apps/authentication`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`User`** | `civora_users` | Platform-wide | Unique `email`, nullable unique `phone_number`, COREN registration fields, 10-step onboarding state, `is_platform_admin`. |
| **`Account`** | `civora_auth_accounts` | Linked to `User` | `uq_auth_provider_account_id` (`provider`, `provider_account_id`), tracks `credentials`, `google`, `oidc`, `whatsapp`, `telegram`. |
| **`Session`** | `civora_auth_sessions` | Linked to `User` & optional `Organization` | Unique `jti`, `client_surface` (`web_pe`, `web_stakeholder`, `web_admin`, `mobile_app`), `is_revoked`, `expires_at`. |

### 2.2 Organization (`apps/organizations`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`Organization`** | `civora_organizations` | Root Tenant | Unique `slug`, unique `code`, `tier`, `cac_number`, `compliance_threshold_percent`. |
| **`OrganizationMembership`** | `civora_organization_memberships` | Tenant-scoped | `uq_organization_user_membership` (`organization`, `user`), `role` (`PROTECT`). |
| **`OrganizationInvitation`** | `civora_organization_invitations` | Tenant-scoped | Unique `invitation_code`, `email`, `role` (`PROTECT`), `status`, `expires_at`. |

### 2.3 Access Control (`apps/access_control`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`Role`** | `civora_roles` | System RBAC | Unique `slug` (`platform_admin`, `project_engineer`, `supervising_reviewer`, `read_only_stakeholder`). |
| **`Permission`** | `civora_permissions` | System RBAC | Unique `codename` (e.g., `submissions.verify`, `reports.draft`, `reports.approve`, `reports.release`). |
| **`RolePermission`** | `civora_role_permissions` | System RBAC | `uq_role_permission` (`role`, `permission`). |

### 2.4 Projects (`apps/projects`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`Project`** | `civora_projects` | `TenantScopedModel` | `uq_organization_project_code` (`organization`, `code`), controlled lifecycle `status` state machine. |
| **`ProjectDepartment`** | `civora_project_departments` | `ProjectScopedModel` | `uq_project_department_code` (`project`, `code`). |
| **`ProjectMembership`** | `civora_project_memberships` | `ProjectScopedModel` | `uq_project_user_membership` (`project`, `user`), validates department belongs to project. |
| **`ProjectActivity`** | `civora_project_activities` | `ProjectScopedModel` | `uq_project_activity_code` (`project`, `code`), WBS activity tracking (`planned`, `in_progress`, `completed`, `delayed`, `blocked`). |

### 2.5 Field Operations (`apps/submissions` & `apps/evidence`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`Submission`** | `civora_submissions` | `ProjectScopedModel` | `uq_project_submission_reference` (`project`, `reference_code`), `channel` (`mobile_app`, `web_portal`, `whatsapp`, `telegram`), GPS & completeness telemetry. |
| **`SubmissionStatusHistory`** | `civora_submission_status_history` | `ProjectScopedModel` | Tracks every transition (`from_status` -> `to_status`) with `changed_by` and `reason`. |
| **`SubmissionAttachment`** | `civora_submission_attachments` | `ProjectScopedModel` | Unique `storage_key`, `sha256_checksum`, `media_type`, EXIF/GPS metadata, `is_verified`. |

### 2.6 Tasks (`apps/tasks`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`Task`** | `civora_tasks` | `ProjectScopedModel` | `uq_project_task_code` (`project`, `code`), `status`, `priority`, `due_date`, `requires_evidence`. |
| **`TaskAssignment`** | `civora_task_assignments` | `ProjectScopedModel` | `uq_task_assignee` (`task`, `assignee`), `role_in_task`. |
| **`TaskCompletionEvidence`** | `civora_task_completion_evidence` | `ProjectScopedModel` | Links `Task` to verified `Submission` and/or `SubmissionAttachment` within the same project. |

### 2.7 Reporting (`apps/reports`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`ReportTemplate`** | `civora_report_templates` | `TenantScopedModel` | `uq_org_report_template_code` (`organization`, `code`), JSON `schema_definition` & `default_sections`. |
| **`Report`** | `civora_reports` | `ProjectScopedModel` | `uq_project_report_reference` (`project`, `reference_code`), `status`, `current_version_number`, `published_at`. |
| **`ReportVersion`** | `civora_report_versions` | `ProjectScopedModel` | `uq_report_version_number` (`report`, `version_number`), `checksum_sha256`, `is_immutable`. |
| **`ReportSection`** | `civora_report_sections` | `ProjectScopedModel` | Ordered narrative/tabular sections (`order_index`, `section_key`, `content_markdown`, `structured_data`). |
| **`ReportEvidenceLink`** | `civora_report_evidence_links` | `ProjectScopedModel` | Traceable citations linking `Report`/`ReportSection` to `Submission` and `SubmissionAttachment`. |

### 2.8 Governance (`apps/audit` & `apps/approvals`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`AuditLog`** | `civora_audit_logs` | Append-only | Immutable (`save()` on update and `delete()` raise `PermissionDenied`), `SET_NULL` on actor/org/project deletion. |
| **`ReportReview`** | `civora_report_reviews` | `ProjectScopedModel` | Tracks supervising reviewer decision (`pending`, `approved`, `changes_requested`, `rejected`). |
| **`ReviewComment`** | `civora_review_comments` | `ProjectScopedModel` | Section-level or general review feedback with resolution tracking (`is_resolved`, `resolved_by`). |
| **`Approval`** | `civora_approvals` | `ProjectScopedModel` | `uq_approval_report_version_approver`, immutable regulatory sign-off (`PROTECT` on `report`, `report_version`, `approver`). |

### 2.9 Notifications (`apps/notifications`)
| Entity | Table | Scope | Key Relationships & Constraints |
| :--- | :--- | :--- | :--- |
| **`Notification`** | `civora_notifications` | `TenantScopedModel` | Indexed by `(recipient, is_read, created_at)` and `(organization, project, event_type)`. |
| **`NotificationPreference`** | `civora_notification_preferences` | 1:1 with `User` | Per-channel toggles (`in_app`, `email`, `whatsapp`, `telegram`), digest & quiet-hours settings. |
| **`NotificationDeliveryLog`** | `civora_notification_delivery_logs` | `TenantScopedModel` | Tracks outbound delivery status (`queued`, `sent`, `delivered`, `failed`, `skipped`) per channel. |

---

## 3. Retention & Deletion Policies

1. **Immutable Governance & Sign-Off Records (`AuditLog`, `Approval`)**:
   - `AuditLog` and `Approval` enforce application-level immutability (`PermissionDenied` on update or delete).
   - `Approval` uses `models.PROTECT` on `report`, `report_version`, and `approver` so signed engineering dossiers cannot be orphaned or silently erased.
2. **User Deactivation vs. Deletion**:
   - Operational authorship references (`submitted_by`, `reviewed_by`, `prepared_by`, `created_by`, `actor`) use `models.SET_NULL` (or `models.PROTECT` on formal approvals) so historical submissions, tasks, reports, and audit trails remain intact if a user account is deactivated.
3. **RBAC Role Integrity**:
   - `OrganizationMembership.role`, `ProjectMembership.role`, and `OrganizationInvitation.role` use `models.PROTECT` to prevent deletion of active RBAC roles.
