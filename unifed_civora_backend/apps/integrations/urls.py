"""
URL routing configuration for external channel integrations (Phase 6).
Mounted under `/api/v1/integrations/`.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ChannelBindingViewSet,
    ChannelTelemetryView,
    InboundSimulationView,
    InboundWebhookEventListView,
    IngestionRunListView,
    IngestionRunRetryView,
    VerifiedContactViewSet,
)
from .webhooks import TelegramWebhookView, WhatsAppWebhookView

router = DefaultRouter()
router.register(r"bindings", ChannelBindingViewSet, basename="channel-binding")
router.register(r"contacts", VerifiedContactViewSet, basename="verified-contact")

urlpatterns = [
    # Ingress Webhooks
    path("webhooks/whatsapp/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
    path("webhooks/telegram/", TelegramWebhookView.as_view(), name="telegram-webhook"),
    # Simulation & Test Runner
    path("simulate/", InboundSimulationView.as_view(), name="inbound-simulate"),
    # Telemetry & DLQ Monitoring
    path("telemetry/", ChannelTelemetryView.as_view(), name="channel-telemetry"),
    path("events/", InboundWebhookEventListView.as_view(), name="inbound-events"),
    path("runs/", IngestionRunListView.as_view(), name="ingestion-runs"),
    path("runs/<str:run_id>/retry/", IngestionRunRetryView.as_view(), name="ingestion-run-retry"),
    # Router endpoints (bindings, contacts)
    path("", include(router.urls)),
]
