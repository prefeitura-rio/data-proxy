"""Create the console application."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from dbos import DBOSClient
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from console import routes
from console.database import create_engine
from console.problems import Problem, problem_handler, validation_handler
from console.registry import discover_routers
from console.settings import settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from console.state import AppState


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[AppState]:
    """Open the DBOS client and the database engine at startup and close them at shutdown."""
    client = DBOSClient(
        system_database_url=settings.DBOS_SYSTEM_DATABASE_URL,
        dbos_system_schema=settings.DBOS_SYSTEM_SCHEMA,
        lazy=True,
    )
    engine = create_engine(settings.PG_DATABASE_URL)
    catalog = (
        None
        if settings.DUCKLAKE_CATALOG_URL is None
        else create_engine(settings.DUCKLAKE_CATALOG_URL)
    )
    try:
        yield {"client": client, "engine": engine, "catalog": catalog}
    finally:
        await engine.dispose()
        if catalog is not None:
            await catalog.dispose()
        client.destroy()


app = FastAPI(title="Data Proxy sync console", lifespan=lifespan)
app.add_exception_handler(Problem, problem_handler)
app.add_exception_handler(RequestValidationError, validation_handler)

for router in discover_routers(routes):
    app.include_router(router)
