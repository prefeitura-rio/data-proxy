"""Typed test doubles for the data-proxy test suite."""

import contextlib
from collections.abc import Sequence
from datetime import datetime
from typing import cast, final

from google.cloud.bigquery import Client
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

    @property
    def execute_calls(self) -> int:
        return len(self.executed)

    def execute(self, query: object, params: object = None) -> None:
        self.executed.append(query)

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

    def __init__(self, decr_value: int = 1, lag: int = 1) -> None:
        self._decr_value = decr_value
        self._lag = lag
        self.store = {}
        self.set_calls = []

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

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",  # noqa: A002
        mkstream: bool = False,
    ) -> None:
        pass

    async def xinfo_groups(self, name: str) -> list[dict[str, object]]:
        return [{"name": b"workers", "lag": self._lag}]


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
    """Metadata client double exposing get_table and close-call tracking."""

    _modified: datetime | None
    calls: list[str]
    close_calls: int

    def __init__(self, modified: datetime | None = None) -> None:
        self._modified = modified
        self.calls = []
        self.close_calls = 0

    def get_table(self, bq_table: str) -> FakeBigQueryClient:
        """Record the table reference and return metadata for it."""
        self.calls.append(bq_table)
        return self

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
