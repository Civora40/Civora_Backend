"""
API Views for external channel integrations (Phase 6).
Exposes simulation, telemetry, channel binding management, and ingestion run retries.
"""
from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access_control.permissions import HasOrganizationCapability, IsTenantMember

from .adapters import NormalizedAttachment, NormalizedInboundMessage
from .models import (
    ChannelBinding,
    InboundWebhookEvent,
    IngestionRun,
    VerifiedContactMethod,
)
from .serializers import (
    ChannelBindingSerializer,
    ChannelTelemetrySerializer,
    InboundSimulationSerializer,
    InboundWebhookEventSerializer,
    IngestionRunSerializer,
    VerifiedContactMethodSerializer,
)
from .services import (
    get_channel_telemetry,
    process_inbound_message,
    retry_ingestion_run,
)


class InboundSimulationView(APIView):
    """
    Simulation endpoint for testing WhatsApp and Telegram ingestion flows
    directly from the PE or Admin frontend test runner.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = InboundSimulationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        attachments = []
        for att in data.get("attachments", []):
            attachments.append(
                NormalizedAttachment(
                    original_filename=att.get("originalFilename")
                    or att.get("original_filename", "attachment.jpg"),
                    mime_type=att.get("mimeType")
                    or att.get("mime_type", "image/jpeg"),
                    file_size_bytes=att.get("fileSizeBytes")
                    or att.get("file_size_bytes", 1500000),
                    provider_media_id=att.get("providerMediaId")
                    or att.get("provider_media_id", f"med_sim_{data['providerMessageId']}"),
                    category=att.get("category", "site_photo"),
                    simulate_failure=bool(
                        att.get("simulateFailure") or att.get("simulate_failure")
                    ),
                )
            )

        normalized_msg = NormalizedInboundMessage(
            channel=data["channel"],
            provider_event_id=data.get("providerEventId") or f"evt_sim_{data['providerMessageId']}",
            provider_message_id=data["providerMessageId"],
            sender_raw=data["senderRaw"],
            external_chat_id=data.get("externalChatId", ""),
            message_text=data["messageText"],
            project_id=str(data["projectId"]) if data.get("projectId") else None,
            attachments=attachments,
            raw_payload=request.data,
        )

        result = process_inbound_message(normalized_msg)
        http_status = status.HTTP_200_OK
        if result.get("code") == "project_approval_required":
            http_status = status.HTTP_403_FORBIDDEN

        return Response(result, status=http_status)


class ChannelTelemetryView(APIView):
    """Surfaces live telemetry metrics for external gateways to Admin Console."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        data = get_channel_telemetry()
        serializer = ChannelTelemetrySerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class IngestionRunListView(generics.ListCreateAPIView):
    """Lists ingestion runs with failure rates and DLQ counts."""

    queryset = IngestionRun.objects.all().order_by("-started_at")
    serializer_class = IngestionRunSerializer
    permission_classes = [permissions.AllowAny]


class IngestionRunRetryView(APIView):
    """Retries a failed ingestion event or batch."""

    permission_classes = [permissions.AllowAny]

    def post(self, request, run_id, *args, **kwargs):
        result = retry_ingestion_run(str(run_id))
        return Response(result, status=status.HTTP_200_OK)


class InboundWebhookEventListView(generics.ListAPIView):
    """Lists recent inbound webhook events for audit and verification."""

    queryset = InboundWebhookEvent.objects.all().order_by("-created_at")
    serializer_class = InboundWebhookEventSerializer
    permission_classes = [permissions.AllowAny]


class ChannelBindingViewSet(viewsets.ModelViewSet):
    """Manages chat group and external channel bindings to projects."""

    queryset = ChannelBinding.objects.all().order_by("-created_at")
    serializer_class = ChannelBindingSerializer
    permission_classes = [permissions.AllowAny]


class VerifiedContactViewSet(viewsets.ModelViewSet):
    """Manages verified contact methods for users."""

    queryset = VerifiedContactMethod.objects.all().order_by("-created_at")
    serializer_class = VerifiedContactMethodSerializer
    permission_classes = [permissions.AllowAny]
