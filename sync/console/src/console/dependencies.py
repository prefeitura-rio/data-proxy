"""Dependencies that the routes of the console share."""

from typing import Annotated

from dbos import DBOSClient
from fastapi import Depends
from psycopg_pool import AsyncConnectionPool

from console.state import AppRequest


def get_client(request: AppRequest) -> DBOSClient:
    """Return the DBOS client that the lifespan opened at startup."""
    return request.state["client"]


def get_pool(request: AppRequest) -> AsyncConnectionPool:
    """Return the connection pool that the lifespan opened at startup."""
    return request.state["pool"]


Client = Annotated[DBOSClient, Depends(get_client)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]
