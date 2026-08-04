"""Application settings loaded from environment variables."""

from pathlib import Path
from typing import ClassVar

from pydantic import Field
from pydantic.networks import RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

test_env = Path(".env.test")


class Settings(BaseSettings):
    """Settings for the data-proxy sync pipeline."""

    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "env_file": test_env if test_env.exists() else ".env",
    }

    GCS_BUCKET: str = Field(default=...)
    PG_DSN: str = Field(default=...)
    PG_SCHEMA: str = "pic"
    REDIS_URL: RedisDsn = RedisDsn("redis://localhost:6379/0")
    SYNC_CONFIG_PATH: Path = Path("config/sync.json")

    def make_redis(self) -> Redis[bytes]:
        """Return a Redis client from the configured URL."""
        return Redis.from_url(str(self.REDIS_URL))


settings = Settings()
