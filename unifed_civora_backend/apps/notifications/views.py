"""HTTP views for Notifications & Delivery Preferences (Phase 2 & 9)."""
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from apps.organizations.models import Organization
from common.responses import api_success
from .models import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDeliveryLog,
    NotificationPreference,
    NotificationSeverity,
)


def serialize_notification(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "title": n.title,
        "message": n.body,
        "body": n.body,
        "type": n.event_type,
        "eventType": n.event_type,
        "severity": n.severity,
        "category": n.event_type.split(".")[0] if "." in n.event_type else "system",
        "timestamp": n.created_at.isoformat() if n.created_at else "",
        "createdAt": n.created_at.isoformat() if n.created_at else "",
        "read": n.is_read,
        "isRead": n.is_read,
        "actionUrl": n.action_url or "/dashboard",
        "projectId": str(n.project_id) if n.project_id else None,
        "projectName": n.project.name if n.project else None,
    }


class NotificationListCreateView(APIView):
    """GET, POST, PATCH /api/v1/notifications/"""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Notifications"], summary="List user or platform notifications")
    def get(self, request):
        if request.user.is_platform_admin and request.query_params.get("scope") == "all":
            qs = Notification.objects.select_related("project").all()[:50]
        else:
            qs = Notification.objects.filter(recipient=request.user).select_related("project")[:50]

        pref, _ = NotificationPreference.objects.get_or_create(
            user=request.user,
            defaults={"organization": getattr(request, "organization", None)},
        )
        items = [serialize_notification(n) for n in qs]
        return api_success(
            data={
                "notifications": items,
                "unreadCount": sum(1 for item in items if not item["read"]),
                "preferences": {
                    "inAppEnabled": pref.in_app_enabled,
                    "emailEnabled": pref.email_enabled,
                    "whatsappEnabled": pref.whatsapp_enabled,
                    "telegramEnabled": pref.telegram_enabled,
                    "dailyDigestEnabled": pref.daily_digest_enabled,
                    "escalationAlertsEnabled": pref.escalation_alerts_enabled,
                },
            }
        )

    @extend_schema(tags=["Notifications"], summary="Create notification or mark notifications as read")
    def post(self, request):
        body = request.data or {}
        action = body.get("action")

        if action == "mark_all_read":
            Notification.objects.filter(recipient=request.user, is_read=False).update(
                is_read=True, read_at=timezone.now()
            )
            return api_success(data={"markedAllRead": True}, message="All notifications marked as read.")

        if action == "mark_read" and body.get("notificationId"):
            Notification.objects.filter(
                recipient=request.user, id=body["notificationId"]
            ).update(is_read=True, read_at=timezone.now())
            return api_success(data={"markedRead": True}, message="Notification marked as read.")

        org = getattr(request, "organization", None) or (
            request.user.organization_memberships.first().organization
            if request.user.organization_memberships.exists()
            else Organization.objects.first()
        )
        notif = Notification.objects.create(
            organization=org,
            recipient=request.user,
            actor=request.user,
            event_type=body.get("eventType") or "system.notice",
            severity=body.get("severity") or NotificationSeverity.INFO,
            title=body.get("title") or "Platform Notification",
            body=body.get("body") or body.get("message") or "",
            action_url=body.get("actionUrl") or "/dashboard",
        )
        NotificationDeliveryLog.objects.create(
            organization=org,
            notification=notif,
            channel=NotificationChannel.IN_APP,
            recipient_address=request.user.email,
            status=DeliveryStatus.DELIVERED,
            delivered_at=timezone.now(),
        )
        return api_success(
            data=serialize_notification(notif),
            message="Notification dispatched.",
            status_code=status.HTTP_201_CREATED,
        )
