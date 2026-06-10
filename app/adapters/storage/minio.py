import logging

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MinioStorage:
    def __init__(self) -> None:
        self.client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self.bucket = settings.MINIO_BUCKET

    async def ensure_bucket(self) -> None:
        try:
            exists = self.client.bucket_exists(self.bucket)
            if not exists:
                self.client.make_bucket(self.bucket)
                logger.info("Created bucket %s", self.bucket)
        except S3Error as e:
            logger.error("MinIO bucket check failed: %s", e)
            raise

    async def object_exists(self, storage_key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, storage_key)
            return True
        except S3Error:
            return False

    async def upload(
        self, storage_key: str, content: bytes, content_type: str = "application/pdf"
    ) -> None:
        from io import BytesIO

        self.client.put_object(
            bucket_name=self.bucket,
            object_name=storage_key,
            data=BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
        logger.info("Uploaded %s (%s bytes) to MinIO", storage_key, len(content))

    async def get_presigned_url(self, storage_key: str, expires: int = 3600) -> str | None:
        try:
            from datetime import timedelta
            return self.client.presigned_get_object(self.bucket, storage_key, expires=timedelta(seconds=expires))
        except S3Error:
            return None


minio_storage: MinioStorage = MinioStorage()
