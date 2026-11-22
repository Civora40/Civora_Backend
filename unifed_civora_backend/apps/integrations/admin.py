from django.contrib import admin
from .models import (
    ChannelBinding,
    InboundWebhookEvent,
    IngestionRun,
    VerifiedContactMethod,
)


@admin.register(InboundWebhookEvent)
class InboundWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "channel",
        "provider_message_id",
        "sender_raw",
        "status",
        "project",
        "created_at",
    )
    list_filter = ("channel", "status", "created_at")
    search_fields = ("provider_message_id", "sender_raw", "provider_event_id")
    readonly_fields = ("created_at", "updated_at", "raw_payload")


@admin.register(ChannelBinding)
class ChannelBindingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "channel",
        "label",
        "external_identifier",
        "project",
        "department",
        "is_active",
    )
    list_filter = ("channel", "is_active", "project")
    search_fields = ("label", "external_identifier", "project__name", "project__code")


@admin.register(VerifiedContactMethod)
class VerifiedContactMethodAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "contact_type",
        "identifier",
        "is_active",
        "is_primary",
        "verified_at",
    )
    list_filter = ("contact_type", "is_active", "is_primary")
    search_fields = ("identifier", "user__email", "user__first_name", "user__last_name")


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = (
        "batch_reference",
        "channel",
        "status",
        "total_messages_ingested",
        "duplicates_blocked",
        "duration_ms",
        "started_at",
    )
    list_filter = ("channel", "status", "started_at")
    search_fields = ("batch_reference", "error_log_snippet")
