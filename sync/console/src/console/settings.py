"""Console settings loaded from environment variables."""

from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings for the operator console."""

    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "env_file": ".env",
    }

    AUTH_AUTHENTICATOR_ROLE: str = "authenticator"
    DBOS_SYSTEM_DATABASE_URL: str = Field(default=...)
    DBOS_SYSTEM_SCHEMA: str = "dbos"
    DUCKLAKE_CATALOG_URL: str | None = None
    PG_DATABASE_URL: str = Field(default=...)
    PROXY_ADMIN_URL: str | None = None
    STREAM_INTERVAL_SECONDS: float = Field(default=2.0, gt=0)


settings = Settings()
