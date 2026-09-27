"""Async PostgreSQL connection wrapper."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import LiteralString

from psycopg import AsyncConnection
from psycopg.cursor_async import AsyncCursor
from psycopg.rows import TupleRow

from .types import PostgresParams


@dataclass(frozen=True, slots=True)
class Postgres:
    """Async view of one PostgreSQL connection."""

    connection: AsyncConnection

    @classmethod
    @asynccontextmanager
    async def connect(cls, dsn: str) -> AsyncGenerator[Postgres]:
        """Open one PostgreSQL connection and close it on exit."""
        conn = await AsyncConnection.connect(dsn)
        try:
            yield cls(connection=conn)
        finally:
            await conn.close()

    async def execute(
        self, sql: LiteralString, *, params: PostgresParams | None = None
    ) -> None:
        """Run one statement."""
        await self.connection.execute(sql, params=params)

    async def query(
        self, sql: LiteralString, *, params: PostgresParams | None = None
    ) -> list[TupleRow]:
        """Run one query and return all rows."""
        cursor: AsyncCursor[TupleRow] = await self.connection.execute(
            sql, params=params
        )
        return await cursor.fetchall()

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self.connection.commit()

    async def rollback(self) -> None:
        """Roll back the current transaction."""
        await self.connection.rollback()

    @asynccontextmanager
    async def atomic(self) -> AsyncGenerator[None]:
        """Commit on success and roll back on exception."""
        try:
            yield
        except Exception:
            await self.connection.rollback()
            raise
        await self.connection.commit()
