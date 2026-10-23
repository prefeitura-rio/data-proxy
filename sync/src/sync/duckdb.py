"""Async view of a synchronous DuckDB connection.

A DuckDB connection serves one query at a time, so concurrent calls on one
instance serialize. Open one instance per concurrent task to run in parallel.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb
from asyncer import asyncify
from duckdb import DuckDBPyConnection
from psycopg.sql import Literal

from .settings import settings
from .sources.registry import sources
from .templates import render_template

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from .types import DatabaseRow, DuckDBParams


def limits_sql() -> str:
    """Render the statements that limit one connection, from the settings."""
    return render_template(
        "duckdb/limits",
        {
            "memory_limit": Literal(settings.DUCKDB_MEMORY_LIMIT),
            "threads": Literal(settings.DUCKDB_THREADS),
            "temp_directory": Literal(settings.DUCKDB_TEMP_DIRECTORY),
        },
    )


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
                "source_extensions": sorted(
                    {
                        extension
                        for source in sources.all()
                        for extension in source.extensions
                    }
                ),
            },
        )

        limits = limits_sql()

        def bootstrap() -> None:
            connection.execute(limits)
            connection.execute(setup)

        try:
            await asyncify(bootstrap)()
            yield cls(connection=connection)
        finally:
            await asyncify(connection.close)()

    async def execute(self, sql: str, *, params: DuckDBParams | None = None) -> None:
        """Run one statement."""

        def run() -> None:
            self.connection.execute(sql, params)

        await asyncify(run)()

    async def query(
        self, sql: str, *, params: DuckDBParams | None = None
    ) -> list[DatabaseRow]:
        """Run one query and return every row."""

        def run() -> list[DatabaseRow]:
            return self.connection.execute(sql, params).fetchall()

        return await asyncify(run)()
