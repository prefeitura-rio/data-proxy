from collections.abc import AsyncGenerator

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from console.database import create_engine
from console.dependencies import get_client, get_engine
from console.main import app
from console.settings import settings
from helpers import FakeClient


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


@pytest.fixture
async def api(engine: AsyncEngine) -> AsyncGenerator[AsyncClient]:
    """Call the console app over HTTP, with the test database and a fake DBOS client."""
    app.dependency_overrides[get_client] = FakeClient
    app.dependency_overrides[get_engine] = lambda: engine
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    app.dependency_overrides.clear()
