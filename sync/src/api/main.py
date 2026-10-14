"""Create the dashboard API application."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from dbos import DBOSClient
from fastapi import FastAPI

from api.routes import health
from sync.settings import settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from api.state import AppState


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


def create_app() -> FastAPI:
    """Build the application with its routes."""
    app = FastAPI(title="Data Proxy sync API", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
