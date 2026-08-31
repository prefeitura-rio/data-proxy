"""Narrow structural interfaces for external data-service clients."""

from collections.abc import Awaitable, Iterable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol, Self

from google.cloud.bigquery import (
    QueryJobConfig,
    RangePartitioning,
    SchemaField,
    TimePartitioning,
)
from psycopg.abc import Params, Query, QueryNoTemplate


class DuckDBConnection(Protocol):
    """DuckDB operations used by the synchronization pipeline."""

    def execute(self, query: str, parameters: object = None) -> Self: ...
    def fetchall(self) -> Iterable[tuple[object, ...]]: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class PostgresExecutor(Protocol):
    """PostgreSQL statement execution used by publication operations."""

    def execute(
        self, query: QueryNoTemplate, params: Params | None = None
    ) -> object: ...


class PostgresCursor(Protocol):
    """PostgreSQL batch execution used by freshness operations."""

    def executemany(self, query: Query, params_seq: Iterable[Params]) -> object: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class PostgresCursorProvider(Protocol):
    """PostgreSQL cursor creation used by freshness operations."""

    def cursor(self, *, binary: Literal[False] = False) -> PostgresCursor: ...


class PostgresTransaction(Protocol):
    """PostgreSQL transaction boundaries used by publication operations."""

    def transaction(self) -> AbstractContextManager[object]: ...


class PostgresCommitter(Protocol):
    """PostgreSQL commits used after incremental shadow preparation."""

    def commit(self) -> None: ...


class PostgresFreshness(
    PostgresExecutor, PostgresCursorProvider, PostgresTransaction, Protocol
):
    """PostgreSQL capabilities used to maintain freshness records."""


class PostgresPublication(PostgresFreshness, PostgresCommitter, Protocol):
    """PostgreSQL capabilities used by the complete publication workflow."""


class PostgresSchema(PostgresExecutor, PostgresCommitter, Protocol):
    """PostgreSQL capabilities used for shared schema initialization."""


class PostgresIncremental(PostgresExecutor, PostgresCommitter, Protocol):
    """PostgreSQL capabilities used to prepare an incremental shadow."""


class RedisPipeline(Protocol):
    """Redis transaction operations used by run-state changes."""

    def __aenter__(self) -> Awaitable[Self]: ...
    def __aexit__(self, *args: object) -> Awaitable[None]: ...
    def watch(self, *keys: str) -> Awaitable[None]: ...
    def get(self, key: str) -> Awaitable[bytes | str | None]: ...
    def hexists(self, key: str, field: str) -> Awaitable[bool]: ...
    def multi(self) -> None: ...
    def hset(self, key: str, field: str, value: object) -> None: ...
    def hdel(self, key: str, field: str) -> None: ...
    def hlen(self, key: str) -> None: ...
    def set(self, key: str, value: object, *, ex: int | None = None) -> None: ...
    def expire(self, key: str, seconds: int) -> None: ...
    def execute(self) -> Awaitable[list[object]]: ...


class RedisRead(Protocol):
    """Redis reads used by synchronization state operations."""

    def get(self, key: str) -> Awaitable[bytes | str | None]: ...
    def hget(self, key: str, field: str) -> Awaitable[bytes | str | None]: ...
    def hvals(self, key: str) -> Awaitable[Sequence[bytes | str]]: ...
    def hlen(self, key: str) -> Awaitable[int]: ...


class RedisTransaction(Protocol):
    """Redis pipeline creation used by optimistic state transactions."""

    def pipeline(self, transaction: bool = True) -> RedisPipeline: ...


class RedisKeyDelete(Protocol):
    """Redis key deletion used by run cleanup."""

    def delete(self, *keys: str) -> Awaitable[int]: ...


class RedisStreamCreate(Protocol):
    """Redis stream group creation used by worker startup."""

    def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",  # noqa: A002
        mkstream: bool = False,
    ) -> Awaitable[object]: ...


class RedisConsumerCleanup(Protocol):
    """Redis consumer inspection and deletion used by worker shutdown."""

    def xpending_range(
        self,
        name: str,
        groupname: str,
        _minimum: str,
        _maximum: str,
        count: int,
        consumername: str | None = None,
    ) -> Awaitable[list[object]]: ...
    def xgroup_delconsumer(
        self, name: str, groupname: str, consumername: str
    ) -> Awaitable[int]: ...


class RedisStreamRead(Protocol):
    """Redis stream reads used to avoid duplicate publication dispatch."""

    def xrange(self, name: str) -> Awaitable[list[object]]: ...


class RedisClient(
    RedisRead,
    RedisTransaction,
    RedisKeyDelete,
    RedisStreamCreate,
    RedisConsumerCleanup,
    RedisStreamRead,
    Protocol,
):
    """Concrete-client boundary contract for synchronization applications."""

    def __aenter__(self) -> Awaitable[Self]: ...
    def __aexit__(self, *args: object) -> Awaitable[None]: ...


class BigQueryTable(Protocol):
    """BigQuery table metadata used by synchronization planning."""

    @property
    def modified(self) -> datetime | None: ...

    @property
    def table_type(self) -> str: ...

    @property
    def range_partitioning(self) -> RangePartitioning | None: ...

    @property
    def time_partitioning(self) -> TimePartitioning | None: ...

    @property
    def schema(self) -> Sequence[SchemaField]: ...


class BigQueryQueryResult(Protocol):
    """BigQuery query job result used for partition metadata."""

    def result(self) -> Iterable[object]: ...


class BigQueryMetadataClient(Protocol):
    """BigQuery metadata operations used by synchronization planning."""

    def get_table(self, table: str) -> BigQueryTable: ...
    def query(
        self, query: str, job_config: QueryJobConfig | None = None
    ) -> BigQueryQueryResult: ...
