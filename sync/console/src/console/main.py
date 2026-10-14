"""Create the console application."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from dbos import DBOSClient
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from console import routes
from console.registry import discover_routers
from console.settings import settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from console.state import AppState


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[AppState]:
    """Open the DBOS client and the connection pool at startup and close them at shutdown."""
    client = DBOSClient(
        system_database_url=settings.DBOS_SYSTEM_DATABASE_URL,
        dbos_system_schema=settings.DBOS_SYSTEM_SCHEMA,
        lazy=True,
    )
    try:
        pool: AsyncConnectionPool = AsyncConnectionPool(
            settings.PG_DATABASE_URL, open=False
        )
        async with pool:
            yield {"client": client, "pool": pool}
    finally:
        client.destroy()


app = FastAPI(title="Data Proxy sync console", lifespan=lifespan)

for router in discover_routers(routes):
    app.include_router(router)
