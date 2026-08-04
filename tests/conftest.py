"""Session-scoped fixtures for the data-proxy test suite."""

import pytest
from pydantic.networks import RedisDsn

from dp.settings import settings


@pytest.fixture(scope="session", autouse=True)
def patch_settings() -> None:
    """Ensure Redis URL points to localhost for tests."""
    settings.REDIS_URL = RedisDsn("redis://localhost:6379/0")
