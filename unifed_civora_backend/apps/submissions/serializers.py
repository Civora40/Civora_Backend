"""DRF Serializers for Field Submissions (Phase 5)."""
from rest_framework import serializers
from .models import Submission, SubmissionChannel, SubmissionStatus, SubmissionType


class SubmissionCreateSerializer(serializers.ModelSerializer):
    projectId = serializers.UUIDField(source="project_id", required=False)
    departmentId = serializers.UUIDField(source="department_id", required=False)
    activityId = serializers.UUIDField(source="activity_id", required=False)
    sourceChannel = serializers.CharField(source="channel", required=False)
    gpsLatitude = serializers.DecimalField(source="gps_latitude", max_digits=9, decimal_places=6, required=False)
    gpsLongitude = serializers.DecimalField(source="gps_longitude", max_digits=9, decimal_places=6, required=False)

    class Meta:
        model = Submission
        fields = [
            "id",
            "projectId",
            "departmentId",
            "activityId",
            "reference_code",
            "sourceChannel",
            "submission_type",
            "title",
            "raw_text",
            "gpsLatitude",
            "gpsLongitude",
            "location_label",
            "weather_condition",
            "submitted_at",
            "structured_payload",
        ]


class SubmissionStatusTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SubmissionStatus.choices)
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class SubmissionLinkTaskSerializer(serializers.Serializer):
    taskId = serializers.UUIDField(required=True)
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class SubmissionRequestInfoSerializer(serializers.Serializer):
    queryText = serializers.CharField(max_length=2000, required=True)
    channel = serializers.CharField(max_length=50, default="whatsapp")
    dueDate = serializers.CharField(max_length=50, required=False, allow_blank=True)
