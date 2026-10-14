from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from console.database import create_engine
from console.settings import settings


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    """Connect to the test database, with empty console tables."""
    engine = create_engine(settings.PG_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE data_proxy.schemas, data_proxy.config_revisions RESTART IDENTITY"
            )
        )
    yield engine
    await engine.dispose()
