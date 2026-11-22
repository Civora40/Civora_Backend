"""
Phase 6 End-to-End Test Suite: WhatsApp & Telegram External Channel Integrations.
Verifies:
  - Meta WhatsApp GET verification challenge (valid token returns 200, invalid returns 403)
  - Meta WhatsApp POST HMAC-SHA256 signature verification (X-Hub-Signature-256)
  - Telegram Bot API POST secret token verification (X-Telegram-Bot-Api-Secret-Token)
  - Idempotency & duplicate suppression (replayed providerMessageId returns 200 duplicate_blocked)
  - Sender identity resolution (E.164 phone normalization, VerifiedContactMethod, User matching)
  - Unmapped sender graceful retention (status needs_verification, submitter_identifier preserved)
  - Project resolution (ChannelBinding chat_id mapping & hashtag fallback)
  - Project governance gate (unapproved projects block submissions with project_approval_required)
  - Media attachment ingestion & cryptographic SHA-256 verification
  - Domain classification heuristics (break tests, rebar, safety, shoring)
  - Admin channel telemetry and DLQ retry mechanics
"""
import hashlib
import hmac
import json
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access_control.models import SystemRoleSlug
from apps.access_control.services import seed_canonical_roles_and_permissions
from apps.evidence.models import SubmissionAttachment
from apps.integrations.models import (
    ChannelBinding,
    ContactType,
    EventStatus,
    ExternalChannel,
    InboundWebhookEvent,
    IngestionRun,
    VerifiedContactMethod,
)
from apps.organizations.models import Organization, OrganizationMembership
from apps.projects.models import (
    Project,
    ProjectApprovalStatus,
    ProjectDepartment,
    ProjectStatus,
)
from apps.submissions.models import Submission, SubmissionChannel, SubmissionStatus
from apps.users.models import User

TEST_WA_SECRET = "test_wa_secret_key_12345"
TEST_WA_TOKEN = "test_wa_verify_token_12345"
TEST_TG_SECRET = "test_tg_secret_token_12345"


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_WEBHOOK_VERIFY_TOKEN=TEST_WA_TOKEN,
    WHATSAPP_APP_SECRET=TEST_WA_SECRET,
    TELEGRAM_ENABLED=True,
    TELEGRAM_WEBHOOK_SECRET_TOKEN=TEST_TG_SECRET,
)
class Phase6ExternalChannelIntegrationsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.roles = seed_canonical_roles_and_permissions()

        # 1. Platform Administrator
        self.admin = User.objects.create_user(
            email="civora40@gmail.com",
            password="Password123#",
            full_name="Civora Platform Administrator",
            is_platform_admin=True,
            is_active=True,
        )

        # 2. Test Organization
        self.org = Organization.objects.create(
            name="Federal Ministry of Works & Highway Infrastructure",
            slug="fmwh-infra",
            is_active=True,
        )

        # 3. Active Approved Project (Case 1)
        self.project_active = Project.objects.create(
            organization=self.org,
            name="Lagos-Calabar Coastal Highway — Section 2",
            code="LCR-S2-2026",
            status=ProjectStatus.ACTIVE,
            approval_status=ProjectApprovalStatus.APPROVED,
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
            start_date=timezone.now().date(),
        )
        self.dept_qa = ProjectDepartment.objects.create(
            project=self.project_active,
            organization=self.org,
            name="QA/QC Materials & Geotechnical",
            code="QAQC",
        )

        # 4. Draft / Unapproved Project (Case 2 - Governance Gate)
        self.project_draft = Project.objects.create(
            organization=self.org,
            name="Unapproved Deep Sea Feeder Canal",
            code="UDS-CANAL-2026",
            status=ProjectStatus.DRAFT,
            approval_status=ProjectApprovalStatus.PENDING_REVIEW,
            start_date=timezone.now().date(),
        )

        # 5. Field Engineers
        self.engineer_chidinma = User.objects.create_user(
            email="chidinma@fmwh.gov.ng",
            password="Password123#",
            full_name="Engr. Chidinma Okonkwo",
            phone_number="+2348034192084",
            is_active=True,
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.engineer_chidinma,
            role=self.roles[SystemRoleSlug.PROJECT_ENGINEER],
            is_active=True,
        )
        VerifiedContactMethod.objects.create(
            user=self.engineer_chidinma,
            contact_type=ContactType.WHATSAPP,
            identifier="+2348034192084",
            is_active=True,
            is_primary=True,
        )
        VerifiedContactMethod.objects.create(
            user=self.engineer_chidinma,
            contact_type=ContactType.TELEGRAM,
            identifier="@chidinma_geo_lead",
            is_active=True,
        )

        # 6. Telegram Group Binding
        self.tg_binding = ChannelBinding.objects.create(
            organization=self.org,
            project=self.project_active,
            department=self.dept_qa,
            channel=ExternalChannel.TELEGRAM,
            external_identifier="-100987654321",
            label="Lagos-Calabar Section 2 QA Field Group",
            is_active=True,
        )

    # -------------------------------------------------------------------------
    # 1. WhatsApp Webhook Challenge Verification (GET)
    # -------------------------------------------------------------------------
    def test_whatsapp_get_challenge_success(self):
        """GET with correct verify_token returns the challenge string with HTTP 200."""
        url = "/api/v1/integrations/webhooks/whatsapp/"
        response = self.client.get(
            url,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": TEST_WA_TOKEN,
                "hub.challenge": "challenge_token_abc_123",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.content.decode("utf-8"), "challenge_token_abc_123")

    def test_whatsapp_get_challenge_invalid_token_fails(self):
        """GET with wrong verify_token returns HTTP 403 Forbidden."""
        url = "/api/v1/integrations/webhooks/whatsapp/"
        response = self.client.get(
            url,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "malicious_spoofed_token",
                "hub.challenge": "challenge_token_abc_123",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # 2. WhatsApp Cryptographic HMAC-SHA256 Signature (POST)
    # -------------------------------------------------------------------------
    def test_whatsapp_post_valid_hmac_signature(self):
        """POST with valid X-Hub-Signature-256 processes the inbound message."""
        url = "/api/v1/integrations/webhooks/whatsapp/"
        payload = {
            "entry": [
                {
                    "id": "evt_wa_entry_001",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "wa_phone_99"},
                                "messages": [
                                    {
                                        "id": "wa_msg_valid_001",
                                        "from": "2348034192084",
                                        "timestamp": "1774329600",
                                        "text": {
                                            "body": "#LCR-S2-2026 Compressive cylinder break test 35.2 MPa at Pier 14."
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        }
        raw_body = json.dumps(payload).encode("utf-8")
        signature = "sha256=" + hmac.new(
            TEST_WA_SECRET.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()

        response = self.client.post(
            url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        res_data = response.json()
        self.assertEqual(res_data["status"], "created")
        self.assertEqual(res_data["submission"]["senderVerificationStatus"], "verified")

        # Verify Submission created in DB
        sub = Submission.objects.get(external_message_id="wa_msg_valid_001")
        self.assertEqual(sub.submitted_by, self.engineer_chidinma)
        self.assertEqual(sub.project, self.project_active)

    def test_whatsapp_post_tampered_hmac_signature_fails(self):
        """POST with forged or mismatched HMAC signature returns HTTP 403 Forbidden."""
        url = "/api/v1/integrations/webhooks/whatsapp/"
        payload = {"entry": [{"id": "evt_tampered"}]}
        raw_body = json.dumps(payload).encode("utf-8")

        response = self.client.post(
            url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=invalid_forged_hash_value",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # 3. Telegram Webhook Secret Token Verification (POST)
    # -------------------------------------------------------------------------
    def test_telegram_post_valid_secret_token(self):
        """POST with valid X-Telegram-Bot-Api-Secret-Token processes update."""
        url = "/api/v1/integrations/webhooks/telegram/"
        payload = {
            "update_id": 998877,
            "message": {
                "message_id": 4321,
                "from": {"id": 112233, "username": "chidinma_geo_lead"},
                "chat": {"id": -100987654321, "type": "supergroup"},
                "text": "Daily diaphragm wall settlement telemetry conforms to tolerance.",
                "date": 1774329600,
            },
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TEST_TG_SECRET,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        res_data = response.json()
        self.assertEqual(res_data["status"], "created")

        # Verify routing to channel bound project & department
        sub = Submission.objects.get(external_message_id="tg_msg_-100987654321_4321")
        self.assertEqual(sub.project, self.project_active)
        self.assertEqual(sub.department, self.dept_qa)
        self.assertEqual(sub.submitted_by, self.engineer_chidinma)

    def test_telegram_post_invalid_secret_token_fails(self):
        """POST with missing or invalid secret token returns HTTP 403 Forbidden."""
        url = "/api/v1/integrations/webhooks/telegram/"
        payload = {"update_id": 1}

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong_token",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # 4. Idempotency & Duplicate Suppression
    # -------------------------------------------------------------------------
    def test_idempotency_duplicate_provider_message_suppressed(self):
        """Replaying identical providerMessageId returns HTTP 200 duplicate_blocked."""
        url = "/api/v1/integrations/simulate/"
        sim_payload = {
            "channel": "whatsapp",
            "providerMessageId": "wa_idempotent_test_001",
            "providerEventId": "evt_idempotent_001",
            "senderRaw": "+2348034192084",
            "projectId": str(self.project_active.id),
            "messageText": "#LCR-S2-2026 Batch inspection at batching plant 1",
            "attachments": [],
        }

        # 1. First Ingestion -> created
        resp1 = self.client.post(url, data=sim_payload, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.json()["status"], "created")
        self.assertEqual(
            Submission.objects.filter(external_message_id="wa_idempotent_test_001").count(),
            1,
        )

        # 2. Replayed Webhook -> duplicate_blocked
        resp2 = self.client.post(url, data=sim_payload, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.json()["status"], "duplicate_blocked")

        # Submission count must remain strictly 1
        self.assertEqual(
            Submission.objects.filter(external_message_id="wa_idempotent_test_001").count(),
            1,
        )

        # Verify duplicate audit event recorded
        dup_event = InboundWebhookEvent.objects.filter(
            status=EventStatus.DUPLICATE_BLOCKED
        ).first()
        self.assertIsNotNone(dup_event)
        self.assertIn("suppressed to prevent double-counting", dup_event.error_message)

    # -------------------------------------------------------------------------
    # 5. Project Governance Gate
    # -------------------------------------------------------------------------
    def test_project_governance_block_on_unapproved_project(self):
        """Field submissions sent to unapproved or draft projects are blocked (HTTP 403)."""
        url = "/api/v1/integrations/simulate/"
        sim_payload = {
            "channel": "telegram",
            "providerMessageId": "tg_draft_block_001",
            "senderRaw": "@chidinma_geo_lead",
            "projectId": str(self.project_draft.id),
            "messageText": "Attempted daily site entry on unapproved canal project.",
            "attachments": [],
        }

        response = self.client.post(url, data=sim_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        res_data = response.json()
        self.assertEqual(res_data["code"], "project_approval_required")

        # Verify no Submission record was created in the database
        self.assertFalse(
            Submission.objects.filter(external_message_id="tg_draft_block_001").exists()
        )

        # Inbound event recorded as failed with governance reason
        failed_event = InboundWebhookEvent.objects.get(
            provider_message_id="tg_draft_block_001"
        )
        self.assertEqual(failed_event.status, EventStatus.FAILED)
        self.assertIn("Platform Administrator", failed_event.error_message)

    # -------------------------------------------------------------------------
    # 6. Sender Identity Resolution (Verified vs Unmapped)
    # -------------------------------------------------------------------------
    def test_unmapped_sender_retained_with_needs_verification(self):
        """Unknown senders are never dropped; submission marked needs_clarification."""
        url = "/api/v1/integrations/simulate/"
        sim_payload = {
            "channel": "whatsapp",
            "providerMessageId": "wa_unknown_sender_001",
            "senderRaw": "+2348099998877",  # Unregistered contractor phone
            "projectId": str(self.project_active.id),
            "messageText": "#LCR-S2-2026 Subcontractor rebar delivery waybill 405.",
            "attachments": [],
        }

        response = self.client.post(url, data=sim_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        res_data = response.json()
        self.assertEqual(res_data["status"], "identity_pending")

        sub = Submission.objects.get(external_message_id="wa_unknown_sender_001")
        self.assertIsNone(sub.submitted_by)
        self.assertEqual(sub.submitter_identifier, "+2348099998877")
        self.assertEqual(sub.status, SubmissionStatus.NEEDS_CLARIFICATION)

    # -------------------------------------------------------------------------
    # 7. Media Streaming & SHA-256 Ingestion
    # -------------------------------------------------------------------------
    def test_media_attachment_streaming_and_sha256(self):
        """Inbound photos are downloaded, hashed with SHA-256, and linked to Submission."""
        url = "/api/v1/integrations/simulate/"
        sim_payload = {
            "channel": "whatsapp",
            "providerMessageId": "wa_photo_msg_001",
            "senderRaw": "+2348034192084",
            "projectId": str(self.project_active.id),
            "messageText": "#LCR-S2-2026 Concrete cylinder crush photo test.",
            "attachments": [
                {
                    "originalFilename": "pier14_break_7d.jpg",
                    "mimeType": "image/jpeg",
                    "fileSizeBytes": 2450000,
                    "providerMediaId": "wa_media_pier14_01",
                    "category": "site_photo",
                }
            ],
        }

        response = self.client.post(url, data=sim_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        sub = Submission.objects.get(external_message_id="wa_photo_msg_001")
        self.assertEqual(sub.attachments.count(), 1)

        att: SubmissionAttachment = sub.attachments.first()
        self.assertTrue(att.file_name.startswith("wa_field_evidence_") or "pier14" in att.file_name)
        self.assertTrue(len(att.sha256_checksum) == 64)  # Valid SHA-256 hex string

    # -------------------------------------------------------------------------
    # 8. Domain Classification Heuristics
    # -------------------------------------------------------------------------
    def test_domain_classification_heuristics(self):
        """Civil engineering terms trigger automatic category and compliance advisory."""
        url = "/api/v1/integrations/simulate/"

        # Break test failure variance
        payload_break = {
            "channel": "whatsapp",
            "providerMessageId": "wa_break_low_01",
            "senderRaw": "+2348034192084",
            "projectId": str(self.project_active.id),
            "messageText": "#LCR-S2-2026 Cylinder break test low result 71% of target 30 MPa.",
            "attachments": [],
        }
        resp = self.client.post(url, data=payload_break, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ai_flag = resp.json()["submission"]["aiFlag"]
        self.assertEqual(ai_flag["status"], "advisory_discrepancy")
        self.assertIn("Compressive", ai_flag["message"])

        # Steel tensile compliance
        payload_steel = {
            "channel": "whatsapp",
            "providerMessageId": "wa_steel_spec_01",
            "senderRaw": "+2348034192084",
            "projectId": str(self.project_active.id),
            "messageText": "#LCR-S2-2026 Mill test certificates for rebar heat 901 conform to ASTM.",
            "attachments": [],
        }
        resp_steel = self.client.post(url, data=payload_steel, format="json")
        self.assertEqual(resp_steel.status_code, status.HTTP_200_OK)
        ai_steel = resp_steel.json()["submission"]["aiFlag"]
        self.assertEqual(ai_steel["status"], "spec_compliant")

    # -------------------------------------------------------------------------
    # 9. Admin Telemetry & DLQ Retry Mechanics
    # -------------------------------------------------------------------------
    def test_admin_channel_telemetry_endpoint(self):
        """GET /api/v1/integrations/telemetry/ returns channel health metrics."""
        url = "/api/v1/integrations/telemetry/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)  # WhatsApp and Telegram

        wa_metric = next(item for item in data if item["channel"] == "whatsapp")
        self.assertEqual(wa_metric["status"], "operational")
        self.assertIn("lastPingAt", wa_metric)

    def test_admin_dlq_retry_re_resolves_sender(self):
        """Retrying an unverified event re-evaluates newly registered contacts."""
        # 1. Unknown sender submits
        url_sim = "/api/v1/integrations/simulate/"
        payload = {
            "channel": "whatsapp",
            "providerMessageId": "wa_retry_test_001",
            "senderRaw": "+2348077776655",
            "projectId": str(self.project_active.id),
            "messageText": "#LCR-S2-2026 Pile cap reinforcement inspection",
            "attachments": [],
        }
        resp_sim = self.client.post(url_sim, data=payload, format="json")
        self.assertEqual(resp_sim.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_sim.json()["status"], "identity_pending")

        event = InboundWebhookEvent.objects.get(provider_message_id="wa_retry_test_001")
        self.assertEqual(event.status, EventStatus.NEEDS_VERIFICATION)

        # 2. Admin registers contact method for a newly onboarded engineer
        new_engineer = User.objects.create_user(
            email="emeka@fmwh.gov.ng",
            password="Password123#",
            full_name="Engr. Emeka Nwosu",
            phone_number="+2348077776655",
            is_active=True,
        )
        VerifiedContactMethod.objects.create(
            user=new_engineer,
            contact_type=ContactType.WHATSAPP,
            identifier="+2348077776655",
            is_active=True,
        )

        # 3. Trigger Retry
        retry_url = f"/api/v1/integrations/runs/{event.id}/retry/"
        retry_resp = self.client.post(retry_url)
        self.assertEqual(retry_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(retry_resp.json()["status"], "success")

        # 4. Verify Submission and Event are now properly attributed
        event.refresh_from_db()
        self.assertEqual(event.status, EventStatus.PROCESSED)
        self.assertEqual(event.submission.submitted_by, new_engineer)
        self.assertEqual(event.submission.status, SubmissionStatus.PENDING_REVIEW)
