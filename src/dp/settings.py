"""Application settings loaded from environment variables."""

from pathlib import Path
from typing import ClassVar

from pydantic.networks import RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis


class Settings(BaseSettings):
    """Settings for the data-proxy sync pipeline."""

    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "env_file": ".env",
    }

    GCS_BUCKET: str = "test-bucket"
    PG_DSN: str = "postgresql://test:test@localhost:5432/test"
    PG_SCHEMA: str = "pic"
    REDIS_URL: RedisDsn = RedisDsn("redis://localhost:6379/0")
    SYNC_CONFIG_PATH: Path = Path("config/sync.json")
    GCS_KEY_ID: str = "minioadmin"
    GCS_SECRET_KEY: str = "minioadmin"  # noqa: S105
    GCS_ENDPOINT: str = "localhost:9000"
    GCS_USE_SSL: str = "false"

    def make_redis(self) -> Redis:
        """Return a Redis client from the configured URL."""
        return Redis.from_url(str(self.REDIS_URL))  # pyright: ignore[reportUnknownMemberType]


settings = Settings()
