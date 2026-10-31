"""RBAC role and permission provisioning services."""
from typing import Dict, List
from django.db import transaction
from .models import Permission, Role, RolePermission, SystemRoleSlug


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

