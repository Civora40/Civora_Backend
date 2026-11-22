"""
Provider-independent messaging adapter base interface (Phase 6).
All external communication gateways (WhatsApp, Telegram) implement this uniform interface.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NormalizedAttachment:
    original_filename: str
    mime_type: str
    file_size_bytes: int
    provider_media_id: str
    category: str = "site_photo"
    simulate_failure: bool = False
    binary_content: Optional[bytes] = None


@dataclass
class NormalizedInboundMessage:
    channel: str
    provider_event_id: str
    provider_message_id: str
    sender_raw: str
    external_chat_id: str = ""
    message_text: str = ""
    timestamp: Optional[str] = None
    project_id: Optional[str] = None
    attachments: List[NormalizedAttachment] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class BaseMessagingAdapter(abc.ABC):
    """Abstract interface defining required behaviors for messaging gateways."""

    @abc.abstractmethod
    def verify_webhook(self, request) -> bool:
        """Verify authenticity of the incoming webhook (HMAC signature or secret token)."""
        pass

    @abc.abstractmethod
    def parse_inbound_event(self, request_data: dict, headers: dict = None) -> Optional[NormalizedInboundMessage]:
        """Normalize raw provider webhook payload into standard Civora message representation."""
        pass

    @abc.abstractmethod
    def download_media(self, media_id: str) -> tuple[bytes, str, str]:
        """
        Download media binary from the provider CDN.
        Returns: (binary_bytes, filename, mime_type)
        """
        pass
