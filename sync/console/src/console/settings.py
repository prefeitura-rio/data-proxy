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

    DBOS_SYSTEM_DATABASE_URL: str = Field(default=...)
    DBOS_SYSTEM_SCHEMA: str = "dbos"


settings = Settings()
