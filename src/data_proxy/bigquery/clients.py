"""Async view of a synchronous BigQuery client.

The underlying client is safe to share and concurrent calls overlap, so one
instance may serve several tasks at once.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from asyncer import asyncify
from google.cloud.bigquery import Client, QueryJobConfig
from google.cloud.bigquery.table import Row, Table


@dataclass(frozen=True, slots=True)
class BigQuery:
    """An async view of one synchronous BigQuery client."""

    client: Client

    @classmethod
    @asynccontextmanager
    async def connect(cls, project: str) -> AsyncGenerator[BigQuery]:
        """Build a client for one project and close it off the event loop."""
        client = await asyncify(Client)(project=project)

        try:
            yield cls(client=client)
        finally:
            await asyncify(client.close)()

    async def get_table(self, table: str) -> Table:
        """Fetch one table's metadata."""
        return await asyncify(self.client.get_table)(table)

    async def rows(self, sql: str, job_config: QueryJobConfig) -> Sequence[Row]:
        """Run one query and return every row."""

        def whole() -> Sequence[Row]:
            return list(self.client.query(sql, job_config=job_config).result())

        return await asyncify(whole)()
