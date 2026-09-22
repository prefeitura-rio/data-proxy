"""Generic async PostgreSQL connection context manager."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection


@asynccontextmanager
async def connect_pg(dsn: str) -> AsyncGenerator[AsyncConnection]:
    """Yield one async PostgreSQL connection and close it on exit."""
    pg_conn = await AsyncConnection.connect(dsn)
    try:
        yield pg_conn
    finally:
        await pg_conn.close()
