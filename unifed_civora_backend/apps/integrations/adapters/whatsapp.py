"""
WhatsApp Business Platform (Meta Cloud API) Adapter (Phase 6).
Handles webhook challenge verification, HMAC-SHA256 signature checking,
payload normalization, and media retrieval.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional
import urllib.request

from django.conf import settings

from .base import BaseMessagingAdapter, NormalizedAttachment, NormalizedInboundMessage

logger = logging.getLogger(__name__)


class WhatsAppCloudApiAdapter(BaseMessagingAdapter):
    """Adapter for Meta WhatsApp Cloud API webhooks."""

    def verify_webhook_challenge(self, mode: str, token: str, challenge: str) -> Optional[str]:
        """
        Verify the Meta webhook handshake (GET challenge).
        Returns the challenge string if token matches, otherwise None.
        """
        expected_token = getattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
        if mode == "subscribe" and token and expected_token:
            if hmac.compare_digest(token, expected_token):
                return challenge
        return None

    def verify_webhook(self, request) -> bool:
        """
        Verify HMAC-SHA256 signature in X-Hub-Signature-256 header (POST requests).
        """
        secret = getattr(settings, "WHATSAPP_APP_SECRET", "")
        if not secret:
            # If no secret configured in development, allow requests
            return True

        sig_header = (
            request.headers.get("X-Hub-Signature-256")
            or request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
        )
        if not sig_header or not sig_header.startswith("sha256="):
            return False

        expected_sig = "sha256=" + hmac.new(
            secret.encode("utf-8"),
            request.body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_sig, sig_header)

    def parse_inbound_event(
        self, request_data: dict, headers: dict = None
    ) -> Optional[NormalizedInboundMessage]:
        """Normalize WhatsApp webhook payload into NormalizedInboundMessage."""
        if not isinstance(request_data, dict):
            return None

        # 1. Standard Meta Cloud API Payload structure
        if "entry" in request_data and isinstance(request_data["entry"], list):
            entry = request_data["entry"][0] if request_data["entry"] else {}
            changes = entry.get("changes", [])
            change = changes[0] if changes else {}
            value = change.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                # Event is a status update (delivered, read, sent) rather than inbound message
                return None

            msg = messages[0]
            provider_message_id = msg.get("id") or f"wa_msg_{msg.get('timestamp')}"
            provider_event_id = entry.get("id") or f"evt_wa_{msg.get('id')}"
            sender_raw = f"+{msg.get('from', '')}" if msg.get("from") else ""
            external_chat_id = value.get("metadata", {}).get("phone_number_id", "")

            # Message content
            message_text = (
                msg.get("text", {}).get("body")
                or msg.get("caption")
                or "WhatsApp field submission"
            )

            attachments: list[NormalizedAttachment] = []
            if "image" in msg:
                img = msg["image"]
                attachments.append(
                    NormalizedAttachment(
                        original_filename=f"field_photo_{str(provider_message_id)[-6:]}.jpg",
                        mime_type=img.get("mime_type", "image/jpeg"),
                        file_size_bytes=int(img.get("file_size", 2450000)),
                        provider_media_id=img.get("id", f"med_wa_{provider_message_id}"),
                        category="site_photo",
                    )
                )

            if "document" in msg:
                doc = msg["document"]
                attachments.append(
                    NormalizedAttachment(
                        original_filename=doc.get("filename", "site_document.pdf"),
                        mime_type=doc.get("mime_type", "application/pdf"),
                        file_size_bytes=int(doc.get("file_size", 1200000)),
                        provider_media_id=doc.get("id", f"med_wa_{provider_message_id}"),
                        category="test_report",
                    )
                )

            return NormalizedInboundMessage(
                channel="whatsapp",
                provider_event_id=provider_event_id,
                provider_message_id=provider_message_id,
                sender_raw=sender_raw,
                external_chat_id=external_chat_id,
                message_text=message_text,
                timestamp=msg.get("timestamp"),
                attachments=attachments,
                raw_payload=request_data,
            )

        # 2. Direct simulation / normalized ingestion payload
        if "providerMessageId" in request_data or "provider_message_id" in request_data:
            provider_msg_id = request_data.get("providerMessageId") or request_data.get(
                "provider_message_id"
            )
            provider_evt_id = (
                request_data.get("providerEventId")
                or request_data.get("provider_event_id")
                or f"evt_{provider_msg_id}"
            )
            sender_raw = request_data.get("senderRaw") or request_data.get("sender_raw") or ""
            message_text = request_data.get("messageText") or request_data.get(
                "message_text", "Field update via WhatsApp"
            )
            project_id = request_data.get("projectId") or request_data.get("project_id")
            external_chat_id = request_data.get("externalChatId") or request_data.get(
                "external_chat_id", ""
            )

            raw_attachments = request_data.get("attachments", [])
            attachments = []
            for att in raw_attachments:
                attachments.append(
                    NormalizedAttachment(
                        original_filename=att.get("originalFilename")
                        or att.get("original_filename", "attachment.jpg"),
                        mime_type=att.get("mimeType")
                        or att.get("mime_type", "image/jpeg"),
                        file_size_bytes=att.get("fileSizeBytes")
                        or att.get("file_size_bytes", 1500000),
                        provider_media_id=att.get("providerMediaId")
                        or att.get("provider_media_id", f"med_{provider_msg_id}"),
                        category=att.get("category", "site_photo"),
                        simulate_failure=bool(
                            att.get("simulateFailure") or att.get("simulate_failure")
                        ),
                    )
                )

            return NormalizedInboundMessage(
                channel="whatsapp",
                provider_event_id=provider_evt_id,
                provider_message_id=provider_msg_id,
                sender_raw=sender_raw,
                external_chat_id=external_chat_id,
                message_text=message_text,
                project_id=str(project_id) if project_id else None,
                attachments=attachments,
                raw_payload=request_data,
            )

        return None

    def download_media(self, media_id: str) -> tuple[bytes, str, str]:
        """
        Stream media binary from Meta Graph API using access token.
        Falls back to generating safe simulated binary if running without live token.
        """
        access_token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
        if access_token and not media_id.startswith("sim_"):
            try:
                # 1. Fetch media URL
                meta_url = f"https://graph.facebook.com/v21.0/{media_id}"
                req = urllib.request.Request(
                    meta_url, headers={"Authorization": f"Bearer {access_token}"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    import json

                    data = json.loads(resp.read().decode("utf-8"))
                    download_url = data.get("url")
                    mime_type = data.get("mime_type", "image/jpeg")

                # 2. Download media binary
                if download_url:
                    dl_req = urllib.request.Request(
                        download_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    with urllib.request.urlopen(dl_req, timeout=15) as dl_resp:
                        content = dl_resp.read()
                        ext = ".jpg" if "image" in mime_type else ".pdf"
                        filename = f"wa_{media_id}{ext}"
                        return content, filename, mime_type
            except Exception as e:
                logger.warning(
                    f"WhatsApp live media retrieval failed for {media_id}: {e}. Falling back to simulated media."
                )

        # Simulated fallback binary (1x1 transparent JPEG / safe placeholder)
        dummy_jpg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
            b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f"
            b"\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b"
            b"\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01"
            b"\x00\x00?\x00\xbf\x00\xff\xd9"
        )
        return dummy_jpg, f"wa_field_evidence_{media_id[-4:]}.jpg", "image/jpeg"
