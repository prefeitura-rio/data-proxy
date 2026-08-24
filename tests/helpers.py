"""Typed test doubles for the data-proxy test suite."""

import contextlib
from collections.abc import Sequence
from datetime import datetime
from typing import cast, final

from google.cloud.bigquery import (
    Client,
    RangePartitioning,
    SchemaField,
    TimePartitioning,
)
from psycopg import Connection
from redis.asyncio import Redis


def postgres_connection(fake: object) -> Connection:
    """Cast a PostgreSQL test double to the production connection type."""
    return cast("Connection[tuple[object, ...]]", fake)


def redis_client(fake: object) -> Redis:
    """Cast a Valkey test double to the production Redis type."""
    return cast(Redis, fake)


def bigquery_client(fake: object) -> Client:
    """Cast a BigQuery metadata test double to the production client type."""
    return cast(Client, fake)


@final
class FakeDuckDBConnection:
    """In-memory DuckDB connection double with call tracking."""

    _rows: Sequence[tuple[object, ...]]
    _describe_rows: Sequence[tuple[object, ...]]
    executed: list[str]

    def __init__(
        self,
        rows: Sequence[tuple[object, ...]] | None = None,
        describe_rows: Sequence[tuple[object, ...]] | None = None,
    ) -> None:
        self._rows = rows or []
        self._describe_rows = describe_rows or []
        self.executed = []

    def execute(self, query: str, parameters: object = None) -> FakeDuckDBConnection:
        self.executed.append(query)
        return self

    def fetchall(self) -> Sequence[tuple[object, ...]]:
        if self.executed and self.executed[-1].strip().upper().startswith("DESCRIBE"):
            return self._describe_rows
        return self._rows

    def __enter__(self) -> FakeDuckDBConnection:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        pass


@final
class FakePgConn:
    """Minimal psycopg connection double with execute call tracking."""

    executed: list[object]

    def __init__(self) -> None:
        self.executed = []

    def execute(self, query: object, _params: object = None) -> FakePgConn:
        self.executed.append(query)
        return self

    def commit(self) -> None:
        """Record an implicit successful commit."""

    def transaction(self) -> contextlib.AbstractContextManager[None]:
        """Return a no-op transaction context manager."""
        return contextlib.nullcontext()

    def __enter__(self) -> FakePgConn:
        return self

    def __exit__(self, *args: object) -> None:
        pass


@final
class FakeRedis:
    """Async Redis double with counters and dictionary-backed values."""

    _decr_value: int
    store: dict[str, str]
    set_calls: list[tuple[str, object, int | None]]
    xtrim_calls: list[tuple[str, str | None]]
    sets: dict[str, set[str]]

    def __init__(self, decr_value: int = 1) -> None:
        self._decr_value = decr_value
        self.store = {}
        self.set_calls = []
        self.xtrim_calls = []
        self.sets = {}

    async def get(self, key: str) -> str | None:
        """Return one stored string value."""
        return self.store.get(key)

    async def decr(self, key: str) -> int:
        return self._decr_value

    async def set(self, key: str, value: object, ex: int | None = None) -> bool:
        """Store one value and record the call."""
        self.store[key] = str(value)
        self.set_calls.append((key, value, ex))
        return True

    async def delete(self, *keys: str) -> int:
        """Remove one or more stored keys."""
        removed = 0
        for key in keys:
            removed += self.store.pop(key, None) is not None
            removed += self.sets.pop(key, None) is not None
        return removed

    async def sadd(self, key: str, *values: object) -> int:
        """Add values to one set."""
        members = self.sets.setdefault(key, set())
        before = len(members)
        members.update(str(value) for value in values)
        return len(members) - before

    async def smembers(self, key: str) -> set[str]:
        """Return members of one set."""
        return self.sets.get(key, set())

    async def expire(self, key: str, _seconds: int) -> bool:
        """Accept a TTL for an existing test key."""
        return key in self.store or key in self.sets

    async def xtrim(
        self,
        name: str,
        _maxlen: int | None = None,
        approximate: bool = True,
        minid: str | None = None,
        _limit: int | None = None,
    ) -> int:
        """Record one trim call."""
        self.xtrim_calls.append((name, minid))
        return 0

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",  # noqa: A002
        mkstream: bool = False,
    ) -> None:
        pass


@final
class FakeRedisCM[T]:
    """Async context manager wrapping any Redis double."""

    _redis: T

    def __init__(self, redis: T) -> None:
        self._redis = redis

    async def __aenter__(self) -> T:
        return self._redis

    async def __aexit__(self, *args: object) -> None:
        pass


@final
class FakeBigQueryClient:
    """Metadata client double exposing table and partition metadata."""

    _modified: datetime | None
    calls: list[str]
    close_calls: int

    def __init__(
        self,
        modified: datetime | None = None,
        *,
        range_partitioning: RangePartitioning | None = None,
        time_partitioning: TimePartitioning | None = None,
        rows: list[dict[str, object]] | None = None,
        table_type: str = "TABLE",
    ) -> None:
        self._modified = modified
        self.range_partitioning = range_partitioning
        self.time_partitioning = time_partitioning
        self.rows = rows or []
        self.table_type = table_type
        self.schema = [SchemaField("value", "INTEGER")]
        self.calls = []
        self.query_calls: list[str] = []
        self.close_calls = 0

    def get_table(self, table: str) -> FakeBigQueryClient:
        """Record the table reference and return metadata for it."""
        self.calls.append(table)
        return self

    def query(self, query: str, job_config: object = None) -> FakeBigQueryClient:
        """Record one metadata query and return its rows through result()."""
        self.query_calls.append(query)
        return self

    def result(self) -> list[dict[str, object]]:
        """Return configured metadata rows."""
        return self.rows

    @property
    def modified(self) -> datetime | None:
        """Return the configured modification timestamp."""
        return self._modified

    def close(self) -> None:
        """Record one close operation."""
        self.close_calls += 1


@final
class FakeRedisGroup:
    """Redis double for xgroup_create with call tracking and optional side_effect."""

    calls: list[dict[str, object]]
    side_effect: Exception | None

    def __init__(self, side_effect: Exception | None = None) -> None:
        self.calls = []
        self.side_effect = side_effect

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",  # noqa: A002
        mkstream: bool = False,
    ) -> None:
        self.calls.append(
            {"name": name, "groupname": groupname, "id": id, "mkstream": mkstream}
        )
        if self.side_effect is not None:
            raise self.side_effect
