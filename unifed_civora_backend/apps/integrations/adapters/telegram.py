"""
Telegram Messaging Adapter (Phase 6).
Handles secret token webhook verification, Update object parsing,
Telegram group chat_id extraction, MTProto / App API credentials (api_id, api_hash),
Data Center (DC) endpoints, RSA public key verification, and file streaming.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import socket
import time
from typing import Any, Dict, Optional
import urllib.request

from django.conf import settings

from .base import BaseMessagingAdapter, NormalizedAttachment, NormalizedInboundMessage

logger = logging.getLogger(__name__)


def validate_rsa_public_key(pem_key: str) -> Dict[str, Any]:
    """
    Validates that a PEM RSA public key string parses correctly,
    and returns key metadata including key size and SHA-256 fingerprint.
    """
    clean_pem = pem_key.strip()
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        pk = load_pem_public_key(clean_pem.encode("utf-8"))
        fingerprint = hashlib.sha256(clean_pem.encode("utf-8")).hexdigest()
        return {
            "valid": True,
            "key_size": getattr(pk, "key_size", 2048),
            "algorithm": "RSA",
            "fingerprint": fingerprint,
            "error": None,
        }
    except Exception as exc:
        return {
            "valid": False,
            "key_size": None,
            "algorithm": "RSA",
            "fingerprint": None,
            "error": str(exc),
        }


def probe_telegram_dc_connectivity(
    ip: Optional[str] = None, port: Optional[int] = None, timeout: float = 3.0
) -> Dict[str, Any]:
    """
    Performs a lightweight TCP socket handshake probe to the Telegram Data Center.
    Measures network round-trip latency in milliseconds.
    """
    target_ip = ip or getattr(
        settings,
        "TELEGRAM_DC_PROD_IP" if not getattr(settings, "TELEGRAM_USE_TEST_DC", False) else "TELEGRAM_DC_TEST_IP",
        "149.154.167.50",
    )
    target_port = port or getattr(
        settings,
        "TELEGRAM_DC_PROD_PORT" if not getattr(settings, "TELEGRAM_USE_TEST_DC", False) else "TELEGRAM_DC_TEST_PORT",
        443,
    )

    start_time = time.time()
    try:
        sock = socket.create_connection((target_ip, target_port), timeout=timeout)
        sock.close()
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        return {
            "connected": True,
            "target": f"{target_ip}:{target_port}",
            "latency_ms": elapsed_ms,
            "error": None,
        }
    except Exception as exc:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        return {
            "connected": False,
            "target": f"{target_ip}:{target_port}",
            "latency_ms": elapsed_ms,
            "error": str(exc),
        }


def get_telegram_connection_config() -> Dict[str, Any]:
    """
    Returns sanitized configuration status for the Telegram integration,
    masking secret hashes for safe telemetry and admin inspection.
    """
    app_api_id = getattr(settings, "TELEGRAM_APP_API_ID", 30212373)
    app_api_hash = getattr(settings, "TELEGRAM_APP_API_HASH", "")
    use_test_dc = getattr(settings, "TELEGRAM_USE_TEST_DC", False)

    masked_hash = (
        f"{app_api_hash[:6]}••••{app_api_hash[-4:]}"
        if len(app_api_hash) >= 10
        else "configured" if app_api_hash else "not_configured"
    )

    active_ip = (
        getattr(settings, "TELEGRAM_DC_TEST_IP", "149.154.167.40")
        if use_test_dc
        else getattr(settings, "TELEGRAM_DC_PROD_IP", "149.154.167.50")
    )
    active_port = (
        getattr(settings, "TELEGRAM_DC_TEST_PORT", 443)
        if use_test_dc
        else getattr(settings, "TELEGRAM_DC_PROD_PORT", 443)
    )

    prod_key = getattr(settings, "TELEGRAM_RSA_PUBLIC_KEY_PROD", "")
    test_key = getattr(settings, "TELEGRAM_RSA_PUBLIC_KEY_TEST", "")

    prod_key_meta = validate_rsa_public_key(prod_key) if prod_key else {"valid": False}
    test_key_meta = validate_rsa_public_key(test_key) if test_key else {"valid": False}

    return {
        "enabled": getattr(settings, "TELEGRAM_ENABLED", True),
        "app_api_id": app_api_id,
        "app_api_hash_masked": masked_hash,
        "is_test_dc": use_test_dc,
        "active_dc_endpoint": f"{active_ip}:{active_port}",
        "production_dc_endpoint": f"{getattr(settings, 'TELEGRAM_DC_PROD_IP', '149.154.167.50')}:{getattr(settings, 'TELEGRAM_DC_PROD_PORT', 443)}",
        "test_dc_endpoint": f"{getattr(settings, 'TELEGRAM_DC_TEST_IP', '149.154.167.40')}:{getattr(settings, 'TELEGRAM_DC_TEST_PORT', 443)}",
        "rsa_production_key_valid": prod_key_meta.get("valid", False),
        "rsa_production_key_fingerprint": prod_key_meta.get("fingerprint"),
        "rsa_test_key_valid": test_key_meta.get("valid", False),
        "rsa_test_key_fingerprint": test_key_meta.get("fingerprint"),
        "webhook_configured": bool(getattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "")),
    }


class TelegramBotAdapter(BaseMessagingAdapter):
    """Adapter for Telegram Bot API webhooks and MTProto gateway."""

    def get_config(self) -> Dict[str, Any]:
        """Expose connection configuration for the adapter."""
        return get_telegram_connection_config()

    def probe_connectivity(self, timeout: float = 3.0) -> Dict[str, Any]:
        """Probe live socket connection to Telegram DC."""
        return probe_telegram_dc_connectivity(timeout=timeout)

    def verify_webhook(self, request) -> bool:
        """
        Verify the secret token in X-Telegram-Bot-Api-Secret-Token header.
        """
        secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
        if not secret:
            # If no secret configured in local dev, allow
            return True

        token_header = (
            request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            or request.META.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
        )
        if not token_header:
            return False

        return hmac.compare_digest(secret, token_header)

    def parse_inbound_event(
        self, request_data: dict, headers: dict = None
    ) -> Optional[NormalizedInboundMessage]:
        """Normalize Telegram Update payload into NormalizedInboundMessage."""
        if not isinstance(request_data, dict):
            return None

        # 1. Standard Telegram Update Payload
        if "update_id" in request_data and "message" in request_data:
            msg = request_data["message"]
            chat = msg.get("chat", {})
            sender = msg.get("from", {})

            chat_id = str(chat.get("id", ""))
            message_id = msg.get("message_id")
            update_id = request_data.get("update_id")

            provider_message_id = f"tg_msg_{chat_id}_{message_id}"
            provider_event_id = f"evt_tg_{update_id}"

            # Sender identification: prefer @username, fallback to tg_user_<id>
            username = sender.get("username")
            if username:
                sender_raw = f"@{username}" if not username.startswith("@") else username
            else:
                sender_raw = f"tg_user_{sender.get('id', 'unknown')}"

            message_text = msg.get("text") or msg.get("caption") or "Telegram field submission"

            attachments: list[NormalizedAttachment] = []
            if "photo" in msg and isinstance(msg["photo"], list) and msg["photo"]:
                best_photo = msg["photo"][-1]
                unique_id = best_photo.get("file_unique_id", str(message_id))
                attachments.append(
                    NormalizedAttachment(
                        original_filename=f"tg_photo_{unique_id}.jpg",
                        mime_type="image/jpeg",
                        file_size_bytes=int(best_photo.get("file_size", 1500000)),
                        provider_media_id=best_photo.get("file_id", f"med_tg_{unique_id}"),
                        category="site_photo",
                    )
                )

            if "document" in msg:
                doc = msg["document"]
                attachments.append(
                    NormalizedAttachment(
                        original_filename=doc.get("file_name", "telegram_document.pdf"),
                        mime_type=doc.get("mime_type", "application/pdf"),
                        file_size_bytes=int(doc.get("file_size", 950000)),
                        provider_media_id=doc.get("file_id", f"med_tg_{doc.get('file_unique_id', message_id)}"),
                        category="sensor_log",
                    )
                )

            return NormalizedInboundMessage(
                channel="telegram",
                provider_event_id=provider_event_id,
                provider_message_id=provider_message_id,
                sender_raw=sender_raw,
                external_chat_id=chat_id,
                message_text=message_text,
                timestamp=str(msg.get("date", "")),
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
                "message_text", "Field update via Telegram"
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
                        or att.get("original_filename", "tg_attachment.jpg"),
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
                channel="telegram",
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
        Stream media binary from Telegram Bot API CDN.
        Falls back to safe simulated binary if running without live token.
        """
        bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if bot_token and not media_id.startswith("sim_"):
            try:
                # 1. Get file path
                get_file_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={media_id}"
                with urllib.request.urlopen(get_file_url, timeout=10) as resp:
                    import json

                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("ok"):
                        file_path = data["result"]["file_path"]

                        # 2. Download from file path
                        dl_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                        with urllib.request.urlopen(dl_url, timeout=15) as dl_resp:
                            content = dl_resp.read()
                            filename = file_path.split("/")[-1]
                            mime_type = "image/jpeg" if filename.endswith((".jpg", ".jpeg")) else "application/pdf"
                            return content, filename, mime_type
            except Exception as e:
                logger.warning(
                    f"Telegram live media retrieval failed for {media_id}: {e}. Falling back to simulated media."
                )

        dummy_jpg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
            b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f"
            b"\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b"
            b"\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01"
            b"\x00\x00?\x00\xbf\x00\xff\xd9"
        )
        return dummy_jpg, f"tg_field_evidence_{media_id[-4:]}.jpg", "image/jpeg"
