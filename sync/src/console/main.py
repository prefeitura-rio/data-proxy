"""Create the console application."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from dbos import DBOSClient
from fastapi import FastAPI

from console import routes
from console.registry import discover_routers
from sync.settings import settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from console.state import AppState


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[AppState]:
    """Open the DBOS client when the application starts and close it when it stops."""
    client = DBOSClient(
        system_database_url=settings.DBOS_SYSTEM_DATABASE_URL,
        dbos_system_schema=settings.DBOS_SYSTEM_SCHEMA,
        lazy=True,
    )
    try:
        yield {"client": client}
    finally:
        client.destroy()


app = FastAPI(title="Data Proxy sync console", lifespan=lifespan)

for router in discover_routers(routes):
    app.include_router(router)
