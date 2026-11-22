"""
REST framework serializers for external channel integrations (Phase 6).
Supports channel bindings, verified contact registration, ingestion runs, and telemetry.
"""
from rest_framework import serializers

from .models import (
    ChannelBinding,
    ContactType,
    EventStatus,
    ExternalChannel,
    InboundWebhookEvent,
    IngestionRun,
    VerifiedContactMethod,
)


class InboundWebhookEventSerializer(serializers.ModelSerializer):
    projectName = serializers.CharField(source="project.name", read_only=True)
    projectCode = serializers.CharField(source="project.code", read_only=True)

    class Meta:
        model = InboundWebhookEvent
        fields = [
            "id",
            "channel",
            "provider_event_id",
            "provider_message_id",
            "sender_raw",
            "external_chat_id",
            "status",
            "error_message",
            "organization",
            "project",
            "projectName",
            "projectCode",
            "submission",
            "processed_at",
            "created_at",
        ]
        read_only_fields = fields


class ChannelBindingSerializer(serializers.ModelSerializer):
    projectName = serializers.CharField(source="project.name", read_only=True)
    departmentName = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = ChannelBinding
        fields = [
            "id",
            "organization",
            "project",
            "projectName",
            "department",
            "departmentName",
            "channel",
            "external_identifier",
            "label",
            "is_active",
            "config",
            "created_at",
            "updated_at",
        ]


class VerifiedContactMethodSerializer(serializers.ModelSerializer):
    userEmail = serializers.EmailField(source="user.email", read_only=True)
    userName = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = VerifiedContactMethod
        fields = [
            "id",
            "user",
            "userEmail",
            "userName",
            "contact_type",
            "identifier",
            "is_active",
            "is_primary",
            "verified_at",
            "created_at",
        ]


class IngestionRunSerializer(serializers.ModelSerializer):
    batchReference = serializers.CharField(source="batch_reference")
    startedAt = serializers.DateTimeField(source="started_at")
    completedAt = serializers.DateTimeField(source="completed_at", allow_null=True)
    durationMs = serializers.IntegerField(source="duration_ms")
    totalMessagesIngested = serializers.IntegerField(source="total_messages_ingested")
    duplicatesBlocked = serializers.IntegerField(source="duplicates_blocked")
    unresolvedSenders = serializers.IntegerField(source="unresolved_senders")
    mediaProcessingFailures = serializers.IntegerField(source="media_processing_failures")
    retryAttempts = serializers.IntegerField(source="retry_attempts")
    errorLogSnippet = serializers.CharField(source="error_log_snippet")

    class Meta:
        model = IngestionRun
        fields = [
            "id",
            "channel",
            "status",
            "batchReference",
            "startedAt",
            "completedAt",
            "durationMs",
            "totalMessagesIngested",
            "duplicatesBlocked",
            "unresolvedSenders",
            "mediaProcessingFailures",
            "retryAttempts",
            "errorLogSnippet",
        ]


class ChannelTelemetrySerializer(serializers.Serializer):
    channel = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()
    identifier = serializers.CharField()
    webhookStatus = serializers.CharField()
    lastPingAt = serializers.CharField()
    totalProcessed24h = serializers.IntegerField()
    duplicatesBlocked24h = serializers.IntegerField()
    failureRate24h = serializers.FloatField()
    allowedChatOrGroupCount = serializers.IntegerField()


class InboundSimulationSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=["whatsapp", "telegram"])
    providerMessageId = serializers.CharField(max_length=255)
    providerEventId = serializers.CharField(max_length=255, required=False, allow_blank=True)
    senderRaw = serializers.CharField(max_length=150)
    projectId = serializers.UUIDField(required=False, allow_null=True)
    externalChatId = serializers.CharField(max_length=150, required=False, allow_blank=True)
    messageText = serializers.CharField(allow_blank=True)
    attachments = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )
