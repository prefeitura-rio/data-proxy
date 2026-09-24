"""Async view of a synchronous DuckDB connection.

A DuckDB connection serves one query at a time, so concurrent calls on one
instance serialize. Open one instance per concurrent task to run in parallel.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import duckdb
from asyncer import asyncify
from duckdb import DuckDBPyConnection
from psycopg.sql import Literal

from .settings import settings
from .templates import render_template
from .types import DatabaseRow


@dataclass(frozen=True, slots=True)
class DuckDB:
    """An async view of one synchronous DuckDB connection."""

    connection: DuckDBPyConnection

    @classmethod
    @asynccontextmanager
    async def connect(cls) -> AsyncGenerator[DuckDB]:
        """Open an in-memory connection with S3 secrets loaded."""
        connection = await asyncify(duckdb.connect)()
        setup = render_template(
            "duckdb/setup",
            {
                "s3_key_id": Literal(settings.S3_ACCESS_KEY),
                "s3_secret_key": Literal(settings.S3_SECRET_KEY),
                "s3_endpoint": Literal(settings.S3_ENDPOINT),
                "s3_use_ssl": "true" if settings.S3_USE_SSL else "false",
            },
        )

        def bootstrap() -> None:
            connection.execute(setup)

        try:
            await asyncify(bootstrap)()
            yield cls(connection=connection)
        finally:
            await asyncify(connection.close)()

    async def execute(self, sql: str, params: Sequence[object] | None = None) -> None:
        """Execute a DuckDB statement without returning rows."""

        def run() -> None:
            self.connection.execute(sql, params)

        await asyncify(run)()

    async def fetchall(
        self, sql: str, params: Sequence[object] | None = None
    ) -> list[DatabaseRow]:
        """Run one query and return every row."""

        def whole() -> list[DatabaseRow]:
            return self.connection.execute(sql, params).fetchall()

        return await asyncify(whole)()
