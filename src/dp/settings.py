"""Application settings loaded from environment variables."""

from pathlib import Path
from tempfile import gettempdir
from typing import ClassVar, Literal

from pydantic import Field
from pydantic.networks import RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

from .models import RedisConfig, SchemaWriters, SyncConfig


class Settings(BaseSettings):
    """Settings for the data-proxy sync pipeline."""

    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "env_file": ".env",
    }

    SCHEMA_WRITERS: SchemaWriters = Field(default=...)
    S3_BUCKET: str = "test-bucket"
    PG_DSN: str = "postgresql://test:test@localhost:5432/test"
    REDIS: RedisConfig = RedisConfig(
        read=RedisDsn("redis://localhost:6379/1"),
        write=RedisDsn("redis://localhost:6379/0"),
    )
    SYNC_CONFIG_PATH: Path = Path("config/sync.json")
    S3_ACCESS_KEY: str = "seaweedfs"
    S3_SECRET_KEY: str = "seaweedfs-local"  # noqa: S105
    S3_ENDPOINT: str = "localhost:8333"
    S3_USE_SSL: bool = False
    DUMPER_VISIBILITY_TIMEOUT_MS: int = Field(default=900_000, gt=0)
    SEEDER_VISIBILITY_TIMEOUT_MS: int = Field(default=900_000, gt=0)
    PUBLISHER_VISIBILITY_TIMEOUT_MS: int = Field(default=7_200_000, gt=0)
    PRODUCER_POLL_INTERVAL_SECONDS: int = Field(default=60, gt=0)
    DUMPER_MAX_RETRIES: int = 3
    DUMPER_BATCH_BYTES: int = Field(default=629_145_600, gt=0)
    DUMPER_BATCH_MAX_PARTITIONS: int = Field(default=256, gt=0)
    DUMPER_SCRATCH_DIR: Path = Path(gettempdir())
    AUTH_ANON_ROLE: str = "anon"
    AUTH_USER_ROLE: str = "user"
    AUTH_AUTHENTICATOR_ROLE: str = "authenticator"
    PUSHGATEWAY_URL: str = (
        "http://data-proxy-pushgateway.data-proxy.svc.cluster.local:9091"
    )
    FALLBACK_CACHE_REDIS_DB: int = Field(default=1, ge=0)
    KUBERNETES_NAMESPACE: str = "data-proxy"
    POSTGREST_RO_DEPLOYMENT_TEMPLATE: str = "data-proxy-{}-postgrest-ro"
    POSTGREST_RW_DEPLOYMENT_TEMPLATE: str = "data-proxy-{}-postgrest-rw"
    POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS: int = Field(default=300, gt=0)
    REPLICATION_WAIT_TIMEOUT_SECONDS: int = Field(default=300, gt=0)
    REPLICATION_POLL_INTERVAL_SECONDS: int = Field(default=1, gt=0)

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
        urls = {"read": self.REDIS.read, "write": self.REDIS.write}
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
