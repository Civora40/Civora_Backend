"""
Repeatable development database seeding command (`python manage.py seed_dev`).
Seeds canonical RBAC roles & permissions, the dedicated Platform Admin account
(configured via CIVORA_ADMIN_EMAIL / CIVORA_ADMIN_PASSWORD), multi-tenant organizations,
project engineers, supervising reviewers, read-only stakeholders, invitations, and projects.
"""
from datetime import date, timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.access_control.models import SystemRoleSlug
from apps.access_control.services import seed_canonical_roles_and_permissions
from apps.audit.services import record_audit_event
from apps.organizations.models import Organization, OrganizationInvitation
from apps.organizations.services import add_user_to_organization
from apps.projects.models import Project, ProjectDepartment, ProjectStatus
from apps.projects.services import assign_user_to_project
from apps.users.models import User


class Command(BaseCommand):
    help = "Idempotently seed canonical RBAC roles, permissions, and dedicated Platform Admin while purging demo seed data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--demo",
            action="store_true",
            help="Optionally seed demo tenants, projects, and sample users for local sandbox evaluation.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        seed_demo = bool(options.get("demo"))
        self.stdout.write("Seeding canonical Civora RBAC roles and permissions...")
        roles = seed_canonical_roles_and_permissions()

        # 1. Primary Platform Governance Organization (for Dedicated Platform Admin only)
        org_primary, _ = create_or_update_org(
            name="Civora Core Infrastructure",
            slug="civora-core-infrastructure",
            code="CIV-HQ",
            tier="enterprise",
            cac_number="",
            contact_email="security@civora.internal",
            contact_phone="+234 1 888 2486",
            office_address="Civora House, Victoria Island Technology Corridor, Lagos",
            notes="Platform managing entity and root security boundary.",
            country="Nigeria",
        )

        # 2. Seed Dedicated Platform Admin (Civora40@gmail.com + env-configured admin if different)
        primary_admin_email = "civora40@gmail.com"
        primary_admin_password = "v;H#bRZby'Vc#0mT"
        primary_admin_name = "Civora Platform Administrator"

        admin_user = upsert_user(
            email=primary_admin_email,
            password=primary_admin_password,
            full_name=primary_admin_name,
            job_title="Lead Platform Administrator & Chief Compliance Officer",
            department="Platform Governance & Engineering",
            professional_registration="R.38910",
            coren_status="verified",
            phone_number="+2348010000001",
            is_platform_admin=True,
            is_staff=True,
            is_superuser=True,
        )

        add_user_to_organization(
            organization=org_primary,
            user=admin_user,
            role=roles[SystemRoleSlug.PLATFORM_ADMIN],
        )

        dedicated_admin_email = getattr(settings, "CIVORA_ADMIN_EMAIL", primary_admin_email).strip().lower()
        dedicated_admin_password = getattr(settings, "CIVORA_ADMIN_PASSWORD", primary_admin_password)
        dedicated_admin_name = getattr(settings, "CIVORA_ADMIN_NAME", primary_admin_name)

        if dedicated_admin_email != primary_admin_email:
            env_admin_user = upsert_user(
                email=dedicated_admin_email,
                password=dedicated_admin_password,
                full_name=dedicated_admin_name,
                job_title="Lead Platform Administrator & Chief Compliance Officer",
                department="Platform Governance & Engineering",
                professional_registration="R.38910",
                coren_status="verified",
                phone_number=None,
                is_platform_admin=True,
                is_staff=True,
                is_superuser=True,
            )
            add_user_to_organization(
                organization=org_primary,
                user=env_admin_user,
                role=roles[SystemRoleSlug.PLATFORM_ADMIN],
            )

        # 3. Ensure all real (non-seeded) registered user accounts are linked to their own registered organization
        from apps.users.serializers import is_seeded_demo_user, sync_user_registered_organization

        for real_user in User.objects.all():
            if not is_seeded_demo_user(real_user):
                sync_user_registered_organization(real_user)

        if not seed_demo:
            # Production Mode: Purge any previously seeded demo invitations, projects, users, and organizations
            OrganizationInvitation.objects.filter(invitation_code="CIV-INV-7841").delete()
            Project.objects.filter(code__in=["LAMATA-BL2", "EKO-REVET", "ACC-PRJ-99"]).delete()

            demo_emails = [
                "engineer@civora.internal",
                "marcus.vance@civora.io",
                "elena.rostova@civora.io",
                "stakeholder@civora.internal",
                "engineer@apexcivil.com",
                "babatunde.fashola@civora.internal",
                "amina.bello@civora.internal",
                "admin@civora.io",
            ]
            User.objects.filter(
                email__in=[e for e in demo_emails if e != dedicated_admin_email]
            ).delete()

            Organization.objects.filter(code__in=["JBN-NG", "ACCE-NG"]).delete()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully seeded production RBAC & Dedicated Admin ({dedicated_admin_email}) and purged demo seed records."
                )
            )
            return

        # Optional --demo mode below for local development only
        org_jbn, _ = create_or_update_org(
            name="Julius Berger Nigeria Plc",
            slug="julius-berger-nigeria",
            code="JBN-NG",
            tier="enterprise",
            cac_number="RC-11234",
            contact_email="site.control@juliusberger.com",
            contact_phone="+234 1 270 3000",
            office_address="10 Shettima A. Munguno Crescent, Utako, Abuja",
            notes="Tier 1 Engineering Contractor on Marine & Rail infrastructure.",
            country="Nigeria",
        )

        org_secondary, _ = create_or_update_org(
            name="Apex Coastal Civil Engineering Ltd",
            slug="apex-coastal-civil",
            code="ACCE-NG",
            tier="contractor",
            cac_number="RC-45190",
            contact_email="operations@apexcivil.com",
            contact_phone="+234 1 632 8900",
            office_address="Plot 12, Commercial Block, Victoria Island Extension, Lagos",
            notes="Highway and coastal revetment specialist contractor.",
            country="Nigeria",
        )

        # Also seed secondary admin aliases used by Civora_Admin and Civora Frontend demo selectors
        for alias_email, alias_name, alias_phone in [
            ("babatunde.fashola@civora.internal", "Engr. Babatunde Fashola-Smith", "+2348035550192"),
            ("amina.bello@civora.internal", "Amina Bello, FNSE", "+2348124008912"),
            ("admin@civora.io", "Adebayo Ogunlesi (Platform Admin)", "+2348010000099"),
        ]:
            if alias_email != dedicated_admin_email:
                a_user = upsert_user(
                    email=alias_email,
                    password=dedicated_admin_password,
                    full_name=alias_name,
                    job_title="Senior Systems Auditor & Platform Administrator",
                    department="Platform Governance & Security",
                    professional_registration="R.41209",
                    coren_status="verified",
                    phone_number=alias_phone,
                    is_platform_admin=True,
                    is_staff=True,
                    is_superuser=True,
                )
                add_user_to_organization(
                    organization=org_primary,
                    user=a_user,
                    role=roles[SystemRoleSlug.PLATFORM_ADMIN],
                )

        # 5. Seed Project Engineers, Supervising Reviewers & Read-Only Stakeholders
        pe_user = upsert_user(
            email="engineer@civora.internal",
            password="Civora2026!",
            full_name="Marcus Vance, PE",
            job_title="Senior Structural Project Engineer",
            department="Structural Engineering",
            professional_registration="COREN #CR-89412",
            coren_status="verified",
            phone_number="+2348010000002",
        )
        pe_legacy_user = upsert_user(
            email="marcus.vance@civora.io",
            password="Civora2026!",
            full_name="Marcus Vance, PE",
            job_title="Senior Project Engineer",
            department="Structural Engineering",
            professional_registration="COREN #CR-89412",
            coren_status="verified",
            phone_number="+2348010000012",
        )
        reviewer_user = upsert_user(
            email="elena.rostova@civora.io",
            password="Civora2026!",
            full_name="Engr. Elena Rostova",
            job_title="Supervising Structural Reviewer",
            department="Quality Assurance & Compliance",
            professional_registration="COREN #CR-44210",
            coren_status="verified",
            phone_number="+2348010000003",
        )
        stakeholder_user = upsert_user(
            email="stakeholder@civora.internal",
            password="Civora2026!",
            full_name="Elena Rostova",
            job_title="Capital Programs Oversight Director",
            department="Executive Advisory",
            phone_number="+2348010000004",
        )
        other_tenant_user = upsert_user(
            email="engineer@apexcivil.com",
            password="Civora2026!",
            full_name="Tunde Bakare, PE",
            job_title="Lead Project Engineer",
            department="Coastal Engineering",
            phone_number="+2348010000005",
        )

        # 6. Organization Memberships
        add_user_to_organization(
            organization=org_primary,
            user=admin_user,
            role=roles[SystemRoleSlug.PLATFORM_ADMIN],
        )
        add_user_to_organization(
            organization=org_primary,
            user=pe_user,
            role=roles[SystemRoleSlug.PROJECT_ENGINEER],
            invited_by=admin_user,
        )
        add_user_to_organization(
            organization=org_primary,
            user=pe_legacy_user,
            role=roles[SystemRoleSlug.PROJECT_ENGINEER],
            invited_by=admin_user,
        )
        add_user_to_organization(
            organization=org_primary,
            user=reviewer_user,
            role=roles[SystemRoleSlug.SUPERVISING_REVIEWER],
            invited_by=admin_user,
        )
        add_user_to_organization(
            organization=org_primary,
            user=stakeholder_user,
            role=roles[SystemRoleSlug.READ_ONLY_STAKEHOLDER],
            invited_by=admin_user,
        )
        add_user_to_organization(
            organization=org_secondary,
            user=other_tenant_user,
            role=roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

        # 7. Primary Projects & Departments
        project_1, _ = Project.objects.update_or_create(
            organization=org_primary,
            code="LAMATA-BL2",
            defaults={
                "name": "Lagos Rail Mass Transit (Blue Line) — Viaduct & Station",
                "client_name": "Lagos Metropolitan Area Transport Authority (LAMATA)",
                "location": "Marina — Mile 2 — Okokomaiko Corridor, Lagos",
                "description": "Arterial rail viaduct, substructure piling, and concourse stations.",
                "status": ProjectStatus.ACTIVE,
                "lead_engineer_name": "Marcus Vance, PE (COREN Reg.)",
                "lasbca_permit_number": "LASBCA/INFRA/2025/1102-R",
                "budget_formatted": "₦64.8B",
                "specs_reference": "FMWH Civil Infrastructure Standard & BS 8110 (Eurocode 2)",
                "progress_percent": 68,
                "start_date": date(2026, 1, 15),
                "target_completion_date": date(2027, 6, 30),
                "project_metadata": {
                    "submissionsCount": 142,
                    "pendingReviewsCount": 5,
                    "reportsCount": 12,
                    "lastActivity": "12 mins ago",
                    "submissionChannels": ["whatsapp", "telegram"],
                },
                "created_by": admin_user,
            },
        )

        project_1b, _ = Project.objects.update_or_create(
            organization=org_primary,
            code="EKO-REVET",
            defaults={
                "name": "Eko Atlantic Coastal Revetment & Great Wall Defense",
                "client_name": "South Energyx Nigeria / Lagos State Ministry of Works",
                "location": "Victoria Island Coastal Shoreline, Lagos",
                "description": "Marine bulkhead wall, coastal armor units, and geotechnical instrumentation.",
                "status": ProjectStatus.ACTIVE,
                "lead_engineer_name": "Marcus Vance, PE (COREN Reg.)",
                "lasbca_permit_number": "LASBCA/EKO/2026/0491-B",
                "budget_formatted": "₦42.5B",
                "specs_reference": "BS 6349 Maritime Structures & Coastal Armor Unit Standard",
                "progress_percent": 44,
                "start_date": date(2026, 2, 1),
                "target_completion_date": date(2027, 12, 15),
                "project_metadata": {
                    "submissionsCount": 89,
                    "pendingReviewsCount": 3,
                    "reportsCount": 8,
                    "lastActivity": "1 hour ago",
                    "submissionChannels": ["whatsapp", "telegram"],
                },
                "created_by": admin_user,
            },
        )

        dept_structural, _ = ProjectDepartment.objects.update_or_create(
            project=project_1,
            code="STR-01",
            defaults={
                "organization": org_primary,
                "name": "Structural & Foundation Engineering",
                "description": "Piling, piers, abutments, and reinforced concrete works.",
            },
        )
        dept_qaqc, _ = ProjectDepartment.objects.update_or_create(
            project=project_1,
            code="QAQC-01",
            defaults={
                "organization": org_primary,
                "name": "QA/QC & Materials Testing",
                "description": "Cube crush verification, slump tests, and geotechnical compliance.",
            },
        )

        # 8. Secondary Tenant Project
        project_2, _ = Project.objects.update_or_create(
            organization=org_secondary,
            code="ACC-PRJ-99",
            defaults={
                "name": "Victoria Island Coastal Revetment Phase 3",
                "client_name": "Lagos State Ministry of Waterfront Infrastructure",
                "location": "Victoria Island, Lagos",
                "status": ProjectStatus.ACTIVE,
                "budget_formatted": "₦18.5B",
                "created_by": other_tenant_user,
            },
        )

        # 9. Project Memberships
        for proj in (project_1, project_1b):
            assign_user_to_project(
                project=proj,
                user=admin_user,
                role=roles[SystemRoleSlug.PLATFORM_ADMIN],
                department=dept_structural if proj == project_1 else None,
            )
            assign_user_to_project(
                project=proj,
                user=pe_user,
                role=roles[SystemRoleSlug.PROJECT_ENGINEER],
                department=dept_structural if proj == project_1 else None,
            )
            assign_user_to_project(
                project=proj,
                user=pe_legacy_user,
                role=roles[SystemRoleSlug.PROJECT_ENGINEER],
                department=dept_structural if proj == project_1 else None,
            )
            assign_user_to_project(
                project=proj,
                user=reviewer_user,
                role=roles[SystemRoleSlug.SUPERVISING_REVIEWER],
                department=dept_qaqc if proj == project_1 else None,
            )
            assign_user_to_project(
                project=proj,
                user=stakeholder_user,
                role=roles[SystemRoleSlug.READ_ONLY_STAKEHOLDER],
            )

        assign_user_to_project(
            project=project_2,
            user=other_tenant_user,
            role=roles[SystemRoleSlug.PROJECT_ENGINEER],
        )

        # 10. Seed Sample Invitations
        OrganizationInvitation.objects.update_or_create(
            invitation_code="CIV-INV-7841",
            defaults={
                "organization": org_primary,
                "email": "k.alabi@juliusberger.com",
                "role": roles[SystemRoleSlug.PROJECT_ENGINEER],
                "status": "pending",
                "assigned_project_codes": [project_1.code],
                "invited_by": admin_user,
                "expires_at": timezone.now() + timedelta(days=7),
            },
        )

        # 11. Repair any real (non-seeded) registered user accounts that were previously
        # attached to seeded organizations (e.g. Apex Coastal Civil Engineering Ltd)
        from apps.users.serializers import is_seeded_demo_user, sync_user_registered_organization

        for real_user in User.objects.all():
            if not is_seeded_demo_user(real_user):
                sync_user_registered_organization(real_user)

        # 12. Initial Audit Event
        record_audit_event(
            action="system.seed_dev_completed",
            target_type="Organization",
            target_id=str(org_primary.id),
            actor=admin_user,
            organization=org_primary,
            project=project_1,
            metadata={"project_code": project_1.code, "admin_email": dedicated_admin_email},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded Civora development database (Dedicated Admin: {dedicated_admin_email}, Roles, Permissions, 3 Tenants, Projects & Invitations)."
            )
        )


def create_or_update_org(
    name: str,
    slug: str,
    code: str,
    country: str = "Nigeria",
    tier: str = "enterprise",
    cac_number: str = "",
    contact_email: str = "",
    contact_phone: str = "",
    office_address: str = "",
    notes: str = "",
):
    return Organization.objects.update_or_create(
        code=code,
        defaults={
            "name": name,
            "slug": slug,
            "tier": tier,
            "status": "active",
            "cac_number": cac_number,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
            "office_address": office_address,
            "notes": notes,
            "country": country,
            "is_active": True,
        },
    )


def upsert_user(
    *,
    email: str,
    password: str = "Civora2026!",
    full_name: str,
    job_title: str = "",
    department: str = "",
    professional_registration: str = "",
    coren_status: str = "verified",
    phone_number: str = None,
    is_platform_admin: bool = False,
    is_staff: bool = False,
    is_superuser: bool = False,
) -> User:
    parts = full_name.replace("(Platform Admin)", "").strip().split(" ")
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    if phone_number:
        User.objects.filter(phone_number=phone_number).exclude(email=email.strip().lower()).update(phone_number=None)

    user, _ = User.objects.update_or_create(
        email=email.strip().lower(),
        defaults={
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": full_name,
            "job_title": job_title,
            "department": department,
            "professional_registration": professional_registration,
            "coren_status": coren_status,
            "phone_number": phone_number,
            "account_status": "active",
            "onboarding_step": 10,
            "onboarding_completed_at": timezone.now(),
            "is_email_verified": True,
            "is_phone_verified": bool(phone_number),
            "is_platform_admin": is_platform_admin,
            "is_staff": is_staff,
            "is_superuser": is_superuser,
            "is_active": True,
        },
    )
    user.set_password(password)
    user.save(update_fields=["password"])
    return user
