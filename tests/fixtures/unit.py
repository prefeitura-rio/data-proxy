from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import final
from unittest.mock import AsyncMock

import pytest
from google.cloud.bigquery import Table
from psycopg import AsyncConnection


@final
@dataclass
class RecordingCursor:
    """Record cursor operations for executor tests."""

    operations: list[tuple[str, object]]

    async def execute(self, sql: str, params: object = None) -> None:
        self.operations.append(("execute", (sql, params)))

    async def executemany(self, sql: str, params: object) -> None:
        self.operations.append(("executemany", (sql, params)))


@final
@dataclass
class TransactionConnection:
    """Record transaction operations for atomic tests."""

    commits: int = 0
    rollbacks: int = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@final
class QueryJobDouble:
    """Return rows for a BigQuery query test."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def result(self) -> Iterable[dict[str, object]]:
        return self.rows


@final
class BigQueryClientDouble:
    """Record the client operations used by the facade."""

    def __init__(self) -> None:
        self.project: str | None = None
        self.closed = False
        self.table: Table | None = None
        self.job = QueryJobDouble([])
        self.requested_table: str | None = None
        self.query_text: str | None = None

    def close(self) -> None:
        self.closed = True

    def get_table(self, table: str) -> Table:
        self.requested_table = table
        if self.table is None:
            raise RuntimeError("table double is not configured")
        return self.table

    def query(self, query: str, job_config: object) -> QueryJobDouble:
        self.query_text = query
        return self.job


@final
class PageIterator:
    """Yield configured S3 listing pages."""

    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages

    def __aiter__(self) -> AsyncIterator[dict[str, object]]:
        return self.items()

    async def items(self) -> AsyncIterator[dict[str, object]]:
        for page in self.pages:
            yield page


@final
class PaginatorDouble:
    """Return an asynchronous S3 page iterator."""

    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages
        self.bucket: str | None = None

    def paginate(self, **kwargs: object) -> PageIterator:
        self.bucket = str(kwargs["Bucket"])
        return PageIterator(self.pages)


@final
class S3ClientDouble:
    """Record S3 deletion requests."""

    def __init__(self, paginator: PaginatorDouble) -> None:
        self.paginator = paginator
        self.deletions: list[dict[str, object]] = []

    def get_paginator(self, name: str) -> PaginatorDouble:
        if name != "list_objects_v2":
            raise ValueError(name)
        return self.paginator

    async def delete_objects(self, **request: object) -> None:
        self.deletions.append(request)


@final
class S3ClientContext:
    """Provide an asynchronous S3 client context."""

    def __init__(self, client: S3ClientDouble) -> None:
        self.client = client

    async def __aenter__(self) -> S3ClientDouble:
        return self.client

    async def __aexit__(self, *args: object) -> None:
        return None


@dataclass
class S3BucketDouble:
    """Hold the S3 dependency doubles used by one test."""

    paginator: PaginatorDouble
    client: S3ClientDouble
    context: S3ClientContext


@pytest.fixture
def recording_cursor() -> RecordingCursor:
    """Provide a recording PostgreSQL cursor double."""
    return RecordingCursor(operations=[])


@pytest.fixture
def transaction_connection() -> TransactionConnection:
    """Provide a recording transaction connection double."""
    return TransactionConnection()


@pytest.fixture
def async_connection_double() -> AsyncMock:
    """Provide an async PostgreSQL connection boundary double."""
    return AsyncMock(spec=AsyncConnection)


@pytest.fixture
def bigquery_client_double() -> BigQueryClientDouble:
    """Provide a BigQuery client facade double."""
    return BigQueryClientDouble()


@pytest.fixture
def s3_bucket_double() -> S3BucketDouble:
    """Provide S3 pagination and deletion doubles."""
    pages: list[dict[str, object]] = [
        {"Contents": [{}]},
        {
            "Contents": [
                {"Key": "app/people/data.parquet"},
                {},
                {"Key": "app/events/data.parquet"},
            ]
        },
    ]
    paginator = PaginatorDouble(pages)
    client = S3ClientDouble(paginator)
    return S3BucketDouble(paginator, client, S3ClientContext(client))
