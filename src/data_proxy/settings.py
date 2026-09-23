"""Application settings loaded from environment variables."""

from pathlib import Path
from tempfile import gettempdir
from typing import ClassVar, Literal

from pydantic import Field
from pydantic.networks import RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

from .models import SchemaWriters, SyncConfig


class Settings(BaseSettings):
    """Settings for the data-proxy DBOS sync pipeline."""

    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "env_file": ".env",
    }

    SCHEMA_WRITERS: SchemaWriters = Field(default=...)
    AUTH_ANON_ROLE: str = "anon"
    AUTH_AUTHENTICATOR_ROLE: str = "authenticator"
    AUTH_USER_ROLE: str = "user"
    DBOS_APPLICATION_NAME: str = "data-proxy-pipeline"
    DBOS_APPLICATION_VERSION: str = "0.1.0"
    DBOS_APP_SCHEMA: str = "data_proxy"
    DBOS_SYSTEM_DATABASE_URL: str = Field(default=...)
    DBOS_SYSTEM_SCHEMA: str = "dbos"
    DUMPER_BATCH_BYTES: int = Field(default=629_145_600, gt=0)
    DUMPER_BATCH_MAX_PARTITIONS: int = Field(default=256, gt=0)
    DUMPER_SCRATCH_DIR: Path = Path(gettempdir())
    DUMP_QUEUE_MAX_ATTEMPTS: int = Field(default=3, gt=0)
    DUMP_QUEUE_RATE_LIMIT: int = Field(default=50, gt=0)
    DUMP_QUEUE_WORKER_CONCURRENCY: int = Field(default=4, gt=0)
    FALLBACK_CACHE_REDIS_DB: int = Field(default=1, ge=0)
    KUBERNETES_NAMESPACE: str = "data-proxy"
    OTLP_LOGS_ENDPOINT: str = Field(default="")
    OTLP_METRICS_ENDPOINT: str = Field(default="")
    OTLP_TRACES_ENDPOINT: str = Field(default="")
    PG_DATABASE_URL: str = "postgresql://test:test@localhost:5432/test"
    POSTGREST_RO_DEPLOYMENT_TEMPLATE: str = "data-proxy-{}-postgrest-ro"
    POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS: int = Field(default=300, gt=0)
    POSTGREST_RW_DEPLOYMENT_TEMPLATE: str = "data-proxy-{}-postgrest-rw"
    PUBLISH_QUEUE_WORKER_CONCURRENCY: int = Field(default=4, gt=0)
    REDIS_READ: RedisDsn = RedisDsn("redis://localhost:6379/1")
    REDIS_WRITE: RedisDsn = RedisDsn("redis://localhost:6379/0")
    REPLICATION_POLL_INTERVAL_SECONDS: int = Field(default=1, gt=0)
    REPLICATION_WAIT_TIMEOUT_SECONDS: int = Field(default=300, gt=0)
    S3_ACCESS_KEY: str = "seaweedfs"
    S3_BUCKET: str = "test-bucket"
    S3_ENDPOINT: str = "localhost:8333"
    S3_SECRET_KEY: str = "seaweedfs-local"  # noqa: S105
    S3_USE_SSL: bool = False
    SYNC_CONFIG_PATH: Path = Path("config/sync.json")
    SYNC_QUEUE_CONCURRENCY: int = Field(default=1, gt=0)
    SYNC_RUN_TIMEOUT_SECONDS: int = Field(default=3600, gt=0)
    SYNC_STEP_MAX_ATTEMPTS: int = Field(default=3, gt=0)
    SYNC_SCHEDULE: str = "0 2 * * *"
    SYNC_SCHEDULE_NAME: str = "sync"

    @property
    def sync_config(self) -> SyncConfig:
        """Return the synchronization configuration from the config file."""
        return SyncConfig.model_validate_json(self.SYNC_CONFIG_PATH.read_text())

    def redis(
        self,
        db: int | None = None,
        *,
        role: Literal["read", "write"] = "write",
    ) -> Redis:
        """Return a Redis client for the selected URL."""
        urls = {"read": self.REDIS_READ, "write": self.REDIS_WRITE}
        url = urls[role]
        default_db = int((url.path or "/0").lstrip("/") or 0)

        return Redis(
            host=url.host or "localhost",
            port=url.port or 6379,
            db=db if db is not None else default_db,
            username=url.username,
            password=url.password,
            ssl=url.scheme == "rediss",
        )


settings = Settings()
