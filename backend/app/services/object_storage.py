from pathlib import Path

from app.config.settings import settings


class ObjectStorageClient:
    """Small S3-compatible client wrapper for MinIO-backed media bytes."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region_name = region_name
        self._client = None

    @classmethod
    def from_settings(cls) -> "ObjectStorageClient":
        return cls(
            endpoint_url=settings.OBJECT_STORAGE_ENDPOINT_URL,
            access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY_ID,
            secret_access_key=settings.OBJECT_STORAGE_SECRET_ACCESS_KEY,
            region_name=settings.OBJECT_STORAGE_REGION,
        )

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region_name,
                config=Config(signature_version="s3v4"),
            )
        return self._client

    def download_to_file(
        self,
        *,
        bucket: str,
        object_key: str,
        destination_dir: str,
        filename: str | None = None,
    ) -> str:
        safe_name = Path(filename or object_key).name or "asset-media"
        destination_path = Path(destination_dir) / safe_name
        self.client.download_file(bucket, object_key, str(destination_path))
        return str(destination_path)


def get_object_storage_client() -> ObjectStorageClient:
    return ObjectStorageClient.from_settings()
