"""
Webhook ingress endpoints for WhatsApp Cloud API and Telegram Field Bots (Phase 6).
Enforces cryptographic signature checks and provider handshake challenges.
"""
from __future__ import annotations

import json
import logging
from django.http import HttpResponse, JsonResponse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .adapters import TelegramBotAdapter, WhatsAppCloudApiAdapter
from .services import process_inbound_message

logger = logging.getLogger(__name__)


class WhatsAppWebhookView(APIView):
    """
    WhatsApp Business Cloud API Webhook Handler.
    GET: Challenge verification handshake.
    POST: Inbound message and media processing with HMAC-SHA256 signature verification.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        adapter = WhatsAppCloudApiAdapter()
        verified_challenge = adapter.verify_webhook_challenge(mode, token, challenge)

        if verified_challenge is not None:
            return HttpResponse(verified_challenge, content_type="text/plain", status=200)

        return JsonResponse(
            {"error": "Forbidden: Invalid webhook verify token"},
            status=status.HTTP_403_FORBIDDEN,
        )

    def post(self, request, *args, **kwargs):
        adapter = WhatsAppCloudApiAdapter()

        # 1. Cryptographic HMAC-SHA256 signature verification
        if not adapter.verify_webhook(request):
            return JsonResponse(
                {"error": "Forbidden: Invalid X-Hub-Signature-256 HMAC signature"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # 2. Parse payload
        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception as e:
            return JsonResponse(
                {"error": f"Invalid JSON payload: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalized_msg = adapter.parse_inbound_event(body)
        if not normalized_msg:
            # Acknowledge non-message webhook events (e.g. read/delivered receipt)
            return JsonResponse({"status": "ignored_no_message"}, status=status.HTTP_200_OK)

        # 3. Execute full ingestion pipeline
        result = process_inbound_message(normalized_msg, adapter=adapter)

        http_status = status.HTTP_200_OK
        if result.get("code") == "project_approval_required":
            http_status = status.HTTP_403_FORBIDDEN

        return Response(result, status=http_status)


class TelegramWebhookView(APIView):
    """
    Telegram Bot API Webhook Handler.
    POST: Inbound update processing with secret token header verification.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        adapter = TelegramBotAdapter()

        # 1. Cryptographic secret token header verification
        if not adapter.verify_webhook(request):
            return JsonResponse(
                {"error": "Forbidden: Invalid X-Telegram-Bot-Api-Secret-Token"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # 2. Parse payload
        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception as e:
            return JsonResponse(
                {"error": f"Invalid JSON payload: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalized_msg = adapter.parse_inbound_event(body)
        if not normalized_msg:
            return JsonResponse(
                {"status": "ignored_non_message_update"}, status=status.HTTP_200_OK
            )

        # 3. Execute full ingestion pipeline
        result = process_inbound_message(normalized_msg, adapter=adapter)

        http_status = status.HTTP_200_OK
        if result.get("code") == "project_approval_required":
            http_status = status.HTTP_403_FORBIDDEN

        return Response(result, status=http_status)
