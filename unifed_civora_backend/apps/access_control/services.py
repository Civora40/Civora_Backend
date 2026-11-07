"""RBAC role provisioning and service-level authorization guards (Phase 3)."""
from __future__ import annotations
from typing import Dict, List
from django.db import transaction
from rest_framework.exceptions import PermissionDenied
from apps.audit.services import record_audit_event
from .models import Permission, Role, RolePermission, SystemRoleSlug
from .selectors import user_has_organization_permission, user_has_project_permission


CANONICAL_PERMISSIONS = [
    ("organizations.manage", "Manage Organization Settings", "organizations"),
    ("organizations.invite_member", "Invite & Manage Organization Members", "organizations"),
    ("projects.create", "Create Projects", "projects"),
    ("projects.update", "Update Project Configuration & Lifecycle", "projects"),
    ("projects.view", "View Project Details", "projects"),
    ("projects.manage_members", "Assign Project Engineers & Stakeholders", "projects"),
    ("submissions.create", "Create Field Submissions", "submissions"),
    ("submissions.view", "View Field Submissions & Evidence", "submissions"),
    ("submissions.verify", "Verify or Flag Field Submissions", "submissions"),
    ("tasks.manage", "Create & Assign Project Tasks", "tasks"),
    ("tasks.execute", "Update Assigned Task Progress & Evidence", "tasks"),
    ("reports.draft", "Prepare & Edit Project Reports", "reports"),
    ("reports.review", "Review Reports & Request Revisions", "approvals"),
    ("reports.approve", "Approve Final Reports", "approvals"),
    ("reports.release", "Authorize Final Report Release to Stakeholders", "approvals"),
    ("reports.view_released", "View & Download Released Reports", "reports"),
    ("audit.view", "View Immutable Audit Logs", "audit"),
]

ROLE_CAPABILITY_MATRIX: Dict[str, List[str]] = {
    SystemRoleSlug.PLATFORM_ADMIN: [p[0] for p in CANONICAL_PERMISSIONS],
    SystemRoleSlug.ORGANIZATION_ADMIN: [p[0] for p in CANONICAL_PERMISSIONS],
    SystemRoleSlug.PROJECT_ENGINEER: [
        "projects.view",
        "projects.update",
        "projects.manage_members",
        "submissions.create",
        "submissions.view",
        "submissions.verify",
        "tasks.manage",
        "tasks.execute",
        "reports.draft",
        "reports.view_released",
    ],
    SystemRoleSlug.SUPERVISING_REVIEWER: [
        "projects.view",
        "submissions.view",
        "reports.review",
        "reports.approve",
        "reports.release",
        "reports.view_released",
        "audit.view",
    ],
    SystemRoleSlug.FIELD_PERSONNEL: [
        "projects.view",
        "submissions.create",
        "submissions.view",
        "tasks.execute",
    ],
    SystemRoleSlug.READ_ONLY_STAKEHOLDER: [
        "projects.view",
        "reports.view_released",
    ],
}


@transaction.atomic
def seed_canonical_roles_and_permissions() -> Dict[str, Role]:
    """Idempotently create Civora's canonical permissions and roles."""
    perm_map: Dict[str, Permission] = {}
    for codename, name, module in CANONICAL_PERMISSIONS:
        perm, _ = Permission.objects.update_or_create(
            codename=codename,
            defaults={"name": name, "module": module},
        )
        perm_map[codename] = perm

    role_map: Dict[str, Role] = {}
    for slug, label in SystemRoleSlug.choices:
        role, _ = Role.objects.update_or_create(
            slug=slug,
            defaults={"name": label, "is_system_role": True},
        )
        for perm_code in ROLE_CAPABILITY_MATRIX.get(slug, []):
            RolePermission.objects.get_or_create(
                role=role,
                permission=perm_map[perm_code],
            )
        role_map[slug] = role

    return role_map


def enforce_organization_permission(
    *,
    user,
    organization,
    permission_codename: str,
    target_type: str = "Organization",
    target_id: str = "",
    ip_address: str | None = None,
) -> None:
    """
    Service-level guard verifying explicit organization capability and logging
    unauthorized access attempts to the immutable audit trail.
    """
    if user and getattr(user, "is_platform_admin", False):
        return

    org_id = getattr(organization, "id", organization)
    if user_has_organization_permission(user, org_id, permission_codename):
        return

    record_audit_event(
        action="rbac.permission_denied",
        target_type=target_type,
        target_id=str(target_id or org_id or ""),
        actor=user if (user and getattr(user, "is_authenticated", False)) else None,
        organization=organization if hasattr(organization, "id") else None,
        ip_address=ip_address,
        metadata={
            "required_permission": permission_codename,
            "scope": "organization",
        },
    )
    raise PermissionDenied(f"Insufficient permissions: missing '{permission_codename}' capability.")


def enforce_project_permission(
    *,
    user,
    organization,
    project,
    permission_codename: str,
    target_type: str = "Project",
    target_id: str = "",
    ip_address: str | None = None,
) -> None:
    """
    Service-level guard verifying explicit project membership and capability,
    logging unauthorized access attempts to the immutable audit trail.
    """
    if user and getattr(user, "is_platform_admin", False):
        return

    org_id = getattr(organization, "id", organization)
    proj_id = getattr(project, "id", project)
    if user_has_project_permission(user, org_id, proj_id, permission_codename):
        return

    record_audit_event(
        action="rbac.permission_denied",
        target_type=target_type,
        target_id=str(target_id or proj_id or ""),
        actor=user if (user and getattr(user, "is_authenticated", False)) else None,
        organization=organization if hasattr(organization, "id") else None,
        project=project if hasattr(project, "id") else None,
        ip_address=ip_address,
        metadata={
            "required_permission": permission_codename,
            "scope": "project",
            "project_id": str(proj_id),
        },
    )
    raise PermissionDenied(f"Insufficient project permissions: missing '{permission_codename}' capability.")


def enforce_report_approval_separation_of_duties(
    *,
    user,
    report,
    required_capability: str = "reports.approve",
    ip_address: str | None = None,
) -> None:
    """
    Enforces Phase 3 governance separation of duties:
    1. The user must hold `reports.approve` (or `reports.release`) on the project/organization.
    2. A Project Engineer cannot approve or release their own final report (`report.prepared_by_id == user.id`).
    """
    if user and getattr(user, "is_platform_admin", False):
        return

    # 1. Check explicit capability (`reports.approve` or `reports.release`)
    has_cap = user_has_project_permission(
        user,
        report.organization_id,
        report.project_id,
        required_capability,
    ) or user_has_organization_permission(
        user,
        report.organization_id,
        required_capability,
    )

    if not has_cap:
        record_audit_event(
            action="rbac.separation_of_duties_blocked",
            target_type="Report",
            target_id=str(report.id),
            actor=user,
            organization=report.organization,
            project=report.project,
            ip_address=ip_address,
            metadata={
                "reason": "missing_approval_capability",
                "required_capability": required_capability,
                "prepared_by_id": str(report.prepared_by_id) if report.prepared_by_id else None,
            },
        )
        raise PermissionDenied(
            f"Separation of duties: You do not hold '{required_capability}' clearance to approve or release final reports."
        )

    # 2. Prevent self-approval by the report author unless they are an Organization/Platform Admin
    if report.prepared_by_id and str(report.prepared_by_id) == str(user.id):
        is_org_admin = user_has_organization_permission(
            user,
            report.organization_id,
            "organizations.manage",
        )
        if not is_org_admin:
            record_audit_event(
                action="rbac.separation_of_duties_blocked",
                target_type="Report",
                target_id=str(report.id),
                actor=user,
                organization=report.organization,
                project=report.project,
                ip_address=ip_address,
                metadata={
                    "reason": "author_self_approval_prohibited",
                    "required_capability": required_capability,
                },
            )
            raise PermissionDenied(
                "Separation of duties violation: The report author cannot approve or release their own final report."
            )
