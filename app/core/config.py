from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://arms:arms_dev@localhost:5432/arms_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "arms-pdfs"
    MINIO_SECURE: bool = False

    # LLM
    LLM_PROVIDER: str = "fake"
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""

    # PDF
    PDF_MAX_SIZE_MB: int = 50
    PDF_DOWNLOAD_TIMEOUT_SECONDS: int = 120
    PDF_CONNECT_TIMEOUT_SECONDS: int = 30
    PDF_MAX_REDIRECTS: int = 5

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # API
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "ARMS AI Audit System"
    DEBUG: bool = False

    # Processing
    MAX_AUDIT_ATTEMPTS: int = 3
    PDF_RETRY_MAX_ATTEMPTS: int = 3
    PDF_RETRY_BACKOFF_BASE: float = 2.0
    OUTBOX_RECONCILE_INTERVAL_SECONDS: int = 30
    OUTBOX_STALE_AFTER_SECONDS: int = 120

    # Admin
    ARMS_ADMIN_ACCOUNTS: str = ""

    @property
    def admin_account_set(self) -> set[str]:
        return {a.strip() for a in self.ARMS_ADMIN_ACCOUNTS.split(",") if a.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
