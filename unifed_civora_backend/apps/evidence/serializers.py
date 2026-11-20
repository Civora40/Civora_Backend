"""DRF Serializers for Evidence attachments (Phase 5)."""
from rest_framework import serializers
from .models import AttachmentMediaType, SubmissionAttachment
from .services import serialize_attachment


class SubmissionAttachmentSerializer(serializers.ModelSerializer):
    downloadUrl = serializers.CharField(source="file_url", read_only=True)
    projectId = serializers.UUIDField(source="project_id", read_only=True)
    submissionId = serializers.UUIDField(source="submission_id", read_only=True)
    originalFilename = serializers.CharField(source="file_name", read_only=True)
    checksum = serializers.CharField(source="sha256_checksum", read_only=True)
    isVerified = serializers.BooleanField(source="is_verified", read_only=True)

    class Meta:
        model = SubmissionAttachment
        fields = [
            "id",
            "projectId",
            "submissionId",
            "originalFilename",
            "storage_key",
            "downloadUrl",
            "mime_type",
            "media_type",
            "file_size_bytes",
            "checksum",
            "caption",
            "captured_at",
            "gps_latitude",
            "gps_longitude",
            "isVerified",
            "created_at",
        ]


class EvidenceUploadSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
    projectId = serializers.UUIDField(required=False)
    project = serializers.UUIDField(required=False)
    submissionId = serializers.UUIDField(required=False)
    submission = serializers.UUIDField(required=False)
    caption = serializers.CharField(max_length=500, required=False, allow_blank=True)
    capturedAt = serializers.DateTimeField(required=False)
    gpsLatitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False)
    gpsLongitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False)
    mediaType = serializers.ChoiceField(choices=AttachmentMediaType.choices, required=False)


class VerifyEvidenceSerializer(serializers.Serializer):
    is_verified = serializers.BooleanField(default=True)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)
