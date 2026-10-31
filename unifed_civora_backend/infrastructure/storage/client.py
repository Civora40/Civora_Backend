"""Object storage client abstraction for evidence uploads and presigned URLs."""
from typing import Optional


class StorageClient:
    """Adapter interface for private object storage (S3 / GCS / MinIO)."""

    def generate_presigned_download_url(self, storage_key: str, expires_in_seconds: int = 900) -> str:
        raise NotImplementedError("Configure concrete storage provider in Phase 5.")

    def generate_presigned_upload_url(
        self, storage_key: str, content_type: str, expires_in_seconds: int = 900
    ) -> Optional[str]:
        raise NotImplementedError("Configure concrete storage provider in Phase 5.")

