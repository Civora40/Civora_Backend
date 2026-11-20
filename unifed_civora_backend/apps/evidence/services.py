"""Evidence & media attachment services (Phase 5 — Field submission and evidence management)."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from typing import Optional
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.access_control.services import enforce_project_permission
from apps.audit.services import record_audit_event
from .models import AttachmentMediaType, SubmissionAttachment

# Security: Block dangerous executable file extensions
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".bash", ".php", ".py", ".pl", ".cgi",
    ".js", ".vbs", ".scr", ".com", ".pif", ".jar", ".dll", ".so", ".bin",
    ".app", ".apk", ".dmg", ".msi",
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB standard limit


def calculate_file_sha256(file_obj) -> str:
    """Compute cryptographic SHA-256 digest of a file stream without consuming memory."""
    hasher = hashlib.sha256()
    # If the file has a chunks method (Django UploadedFile), use it
    if hasattr(file_obj, "chunks"):
        for chunk in file_obj.chunks():
            hasher.update(chunk)
    else:
        # Otherwise read in 64KB blocks
        for block in iter(lambda: file_obj.read(65536), b""):
            hasher.update(block)
    # Reset pointer to start so subsequent saves can read the full stream
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    return hasher.hexdigest()


def detect_media_type(mime_type: str, file_name: str) -> str:
    """Map MIME type and extension to canonical AttachmentMediaType choice."""
    lower_mime = (mime_type or "").lower()
    lower_name = (file_name or "").lower()

    if lower_mime.startswith("image/"):
        return AttachmentMediaType.PHOTO
    if lower_mime.startswith("video/"):
        return AttachmentMediaType.VIDEO
    if lower_mime.startswith("audio/"):
        return AttachmentMediaType.AUDIO_VOICE_NOTE
    if lower_mime == "application/pdf" or lower_name.endswith(".pdf"):
        return AttachmentMediaType.DOCUMENT_PDF
    if (
        "spreadsheet" in lower_mime
        or "excel" in lower_mime
        or lower_name.endswith((".xlsx", ".xls", ".csv"))
    ):
        return AttachmentMediaType.SPREADSHEET
    if lower_name.endswith((".dwg", ".dxf", ".step", ".stp", ".ifc")):
        return AttachmentMediaType.DRAWING_CAD

    return AttachmentMediaType.OTHER


def sanitize_filename(name: str) -> str:
    """Strip path traversal and hazardous characters from filename."""
    base = os.path.basename(name)
    clean = re.sub(r"[^\w\.\-\_]", "_", base)
    return clean or "unnamed_attachment"


def validate_file_for_upload(file_obj, max_size_bytes: int = MAX_FILE_SIZE_BYTES) -> None:
    """Validate file size and reject hazardous executables."""
    name = getattr(file_obj, "name", "")
    ext = os.path.splitext(name)[1].lower()

    if ext in BLOCKED_EXTENSIONS:
        raise ValidationError(
            {"file": [f"File extension '{ext}' is not permitted for security reasons."]}
        )

    size = getattr(file_obj, "size", None)
    if size is not None and size > max_size_bytes:
        raise ValidationError(
            {"file": [f"File size exceeds maximum permitted limit ({max_size_bytes // (1024 * 1024)} MB)."]}
        )


def save_evidence_file(
    *,
    file_obj,
    organization,
    project,
    submission=None,
    uploaded_by=None,
    caption: str = "",
    captured_at=None,
    gps_latitude=None,
    gps_longitude=None,
    media_type: Optional[str] = None,
    is_verified: bool = False,
) -> SubmissionAttachment:
    """
    Store an uploaded evidence file in private object storage, compute SHA-256,
    and create the SubmissionAttachment record.
    """
    validate_file_for_upload(file_obj)

    raw_name = getattr(file_obj, "name", "attachment")
    clean_name = sanitize_filename(raw_name)
    file_size = getattr(file_obj, "size", 0)

    # Determine MIME type
    mime_type = getattr(file_obj, "content_type", None)
    if not mime_type or mime_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(clean_name)
        mime_type = guessed or "application/octet-stream"

    # Determine media category
    final_media_type = media_type or detect_media_type(mime_type, clean_name)

    # Compute SHA-256 hash
    checksum = calculate_file_sha256(file_obj)

    # Build unique, tenant-isolated storage key
    timestamp_prefix = int(timezone.now().timestamp())
    sub_folder = submission.reference_code if submission else "general"
    storage_key = (
        f"evidence/{organization.code}/{project.code}/{sub_folder}/"
        f"{timestamp_prefix}_{checksum[:8]}_{clean_name}"
    )

    # Save to storage
    actual_path = default_storage.save(storage_key, file_obj)

    # Construct internal stream/download URL
    attachment = SubmissionAttachment.objects.create(
        organization=organization,
        project=project,
        submission=submission,
        uploaded_by=uploaded_by,
        file_name=clean_name,
        storage_key=actual_path,
        mime_type=mime_type,
        media_type=final_media_type,
        file_size_bytes=file_size,
        sha256_checksum=checksum,
        caption=caption or "",
        captured_at=captured_at or timezone.now(),
        gps_latitude=gps_latitude,
        gps_longitude=gps_longitude,
        is_verified=is_verified,
    )

    attachment.file_url = f"https://amijad-civora.com/api/v1/evidence/{attachment.id}/download/"
    attachment.save(update_fields=["file_url"])

    return attachment


def verify_attachment(
    *,
    attachment: SubmissionAttachment,
    actor,
    is_verified: bool = True,
    notes: str = "",
) -> SubmissionAttachment:
    """Verify or flag an evidence attachment."""
    enforce_project_permission(
        user=actor,
        organization=attachment.organization,
        project=attachment.project,
        permission_codename="submissions.verify",
        target_type="SubmissionAttachment",
        target_id=str(attachment.id),
    )

    attachment.is_verified = is_verified
    attachment.save(update_fields=["is_verified", "updated_at"])

    record_audit_event(
        action="evidence.verified" if is_verified else "evidence.unverified",
        target_type="SubmissionAttachment",
        target_id=str(attachment.id),
        actor=actor,
        organization=attachment.organization,
        project=attachment.project,
        metadata={
            "file_name": attachment.file_name,
            "sha256_checksum": attachment.sha256_checksum,
            "notes": notes,
        },
    )

    return attachment


def format_file_size(size_bytes: int) -> str:
    """Format bytes into readable KB/MB representation."""
    if not size_bytes:
        return "0 KB"
    kb = size_bytes / 1024
    if kb < 1024:
        return f"{max(1, round(kb))} KB"
    return f"{round(kb / 1024, 1)} MB"


def serialize_attachment(att: SubmissionAttachment) -> dict:
    """Serialize SubmissionAttachment into standard API response dictionary."""
    size_fmt = format_file_size(att.file_size_bytes)
    return {
        "id": str(att.id),
        "submissionId": str(att.submission_id) if att.submission_id else None,
        "submissionIdentifier": att.submission.reference_code if att.submission else None,
        "projectId": str(att.project_id),
        "projectName": att.project.name if att.project else "",
        "originalFilename": att.file_name,
        "storageKey": att.storage_key,
        "mimeType": att.mime_type,
        "mediaType": att.media_type,
        "fileSizeBytes": att.file_size_bytes,
        "fileSizeFormatted": size_fmt,
        "checksum": att.sha256_checksum,
        "caption": att.caption,
        "capturedAt": att.captured_at.isoformat() if att.captured_at else None,
        "gpsLatitude": float(att.gps_latitude) if att.gps_latitude is not None else None,
        "gpsLongitude": float(att.gps_longitude) if att.gps_longitude is not None else None,
        "isVerified": att.is_verified,
        "downloadUrl": att.file_url or f"/api/v1/evidence/{att.id}/download/",
        "processingStatus": "ready",
        "createdAt": att.created_at.isoformat() if att.created_at else None,
    }


def stream_evidence_attachment(attachment: SubmissionAttachment) -> FileResponse:
    """Return an authenticated streaming FileResponse with security headers."""
    if not default_storage.exists(attachment.storage_key):
        raise Http404("Evidence media file does not exist in storage.")

    file_handle = default_storage.open(attachment.storage_key, "rb")
    response = FileResponse(file_handle, content_type=attachment.mime_type)
    response["Content-Disposition"] = f'inline; filename="{attachment.file_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    return response
