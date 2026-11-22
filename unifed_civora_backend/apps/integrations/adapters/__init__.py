"""Adapters package for external messaging channels."""
from .base import BaseMessagingAdapter, NormalizedAttachment, NormalizedInboundMessage
from .telegram import TelegramBotAdapter
from .whatsapp import WhatsAppCloudApiAdapter

__all__ = [
    "BaseMessagingAdapter",
    "NormalizedAttachment",
    "NormalizedInboundMessage",
    "WhatsAppCloudApiAdapter",
    "TelegramBotAdapter",
]
