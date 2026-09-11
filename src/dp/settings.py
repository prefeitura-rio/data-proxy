"""Application settings loaded from environment variables."""

from pathlib import Path
from typing import ClassVar

from pydantic import Field
from pydantic.networks import RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

from .models import SchemaWriters, SyncConfig


class Settings(BaseSettings):
    """Settings for the data-proxy sync pipeline."""

    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "env_file": ".env",
    }

    GCS_BUCKET: str = "test-bucket"
    PG_DSN: str = "postgresql://test:test@localhost:5432/test"
    REDIS_URL: RedisDsn = RedisDsn("redis://localhost:6379/0")
    SYNC_CONFIG_PATH: Path = Path("config/sync.json")
    GCS_KEY_ID: str = "minioadmin"
    GCS_SECRET_KEY: str = "minioadmin"  # noqa: S105
    GCS_ENDPOINT: str = "localhost:9000"
    GCS_USE_SSL: bool = False
    DUMPER_VISIBILITY_TIMEOUT_MS: int = Field(default=900_000, gt=0)
    SEEDER_VISIBILITY_TIMEOUT_MS: int = Field(default=900_000, gt=0)
    PUBLISHER_VISIBILITY_TIMEOUT_MS: int = Field(default=7_200_000, gt=0)
    PRODUCER_POLL_INTERVAL_SECONDS: int = Field(default=60, gt=0)
    DUMPER_MAX_RETRIES: int = 3
    AUTH_ANON_ROLE: str = "anon"
    AUTH_USER_ROLE: str = "user"
    AUTH_AUTHENTICATOR_ROLE: str = "authenticator"
    SCHEMA_WRITERS_FILE: Path = Path("config/schema-writers/writers.json")
    PUSHGATEWAY_URL: str = (
        "http://data-proxy-pushgateway.data-proxy.svc.cluster.local:9091"
    )
    FALLBACK_ENABLED: bool = False
    FALLBACK_CACHE_REDIS_DB: int = Field(default=1, ge=0)

    @property
    def schema_writers(self) -> SchemaWriters:
        """Return the Helm-managed schema-to-writer DSN mapping."""
        try:
            raw = self.SCHEMA_WRITERS_FILE.read_text()
        except FileNotFoundError as error:
            message = f"Schema writers file is unavailable: {self.SCHEMA_WRITERS_FILE}"
            raise RuntimeError(message) from error

        try:
            return SchemaWriters.model_validate_json(raw)
        except ValueError as error:
            message = f"Schema writers file is invalid: {self.SCHEMA_WRITERS_FILE}"
            raise RuntimeError(message) from error

    @property
    def sync_config(self) -> SyncConfig:
        """Return the synchronization configuration from the config file."""
        return SyncConfig.model_validate_json(self.SYNC_CONFIG_PATH.read_text())

    def redis(self, db: int | None = None) -> Redis:
        """Return a Redis client for the configured URL and an optional database number."""
        default_db = int((self.REDIS_URL.path or "/0").lstrip("/") or 0)

        return Redis(
            host=self.REDIS_URL.host or "localhost",
            port=self.REDIS_URL.port or 6379,
            db=db if db is not None else default_db,
            username=self.REDIS_URL.username,
            password=self.REDIS_URL.password,
            ssl=self.REDIS_URL.scheme == "rediss",
        )


settings = Settings()
