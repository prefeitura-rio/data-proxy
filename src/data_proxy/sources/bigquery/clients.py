"""Async view of a synchronous BigQuery client."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from asyncer import asyncify
from google.cloud.bigquery import Client, QueryJobConfig
from google.cloud.bigquery.table import Row, Table

from ...types import BigQueryParams


@dataclass(frozen=True, slots=True)
class BigQuery:
    """An async view of one synchronous BigQuery client."""

    client: Client

    @classmethod
    async def create(cls, project: str) -> BigQuery:
        """Build a client for one project off the event loop."""
        return cls(client=await asyncify(Client)(project=project))

    @classmethod
    @asynccontextmanager
    async def connect(cls, project: str) -> AsyncGenerator[BigQuery]:
        """Build a client for one project and close it off the event loop."""
        connection = await cls.create(project)
        try:
            yield connection
        finally:
            await connection.close()

    async def close(self) -> None:
        """Close the underlying Google client off the event loop."""
        await asyncify(self.client.close)()

    async def get_table(self, table: str) -> Table:
        """Fetch one table's metadata."""
        return await asyncify(self.client.get_table)(table)

    async def execute(self, sql: str, *, params: BigQueryParams | None = None) -> None:
        """Run one statement."""
        if params is None:
            raise TypeError("BigQuery requires query parameters")
        await self.rows(sql, params)

    async def query(
        self, sql: str, *, params: BigQueryParams | None = None
    ) -> Sequence[Row]:
        """Run one query and return every row."""
        if params is None:
            raise TypeError("BigQuery requires query parameters")
        return await self.rows(sql, params)

    async def rows(self, sql: str, params: QueryJobConfig) -> Sequence[Row]:
        """Run one query and return every row."""

        def run() -> Sequence[Row]:
            return list(self.client.query(sql, job_config=params).result())

        return await asyncify(run)()
