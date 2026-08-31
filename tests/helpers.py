"""Typed test doubles for the data-proxy test suite."""

import contextlib
from collections.abc import Iterable, Sequence
from datetime import datetime
from types import TracebackType
from typing import cast, final

from google.cloud.bigquery import (
    RangePartitioning,
    SchemaField,
    TimePartitioning,
)
from redis.exceptions import RedisError, WatchError

from dp.models import AllSelection, DumpTask, PartitionedTablePlan, SyncPlan


def sync_plan(
    *,
    run_id: str = "r1",
    schema_name: str = "app",
    signatures: dict[str, str] | None = None,
    paths: dict[str, list[str]] | None = None,
    partitioned_tables: dict[str, PartitionedTablePlan] | None = None,
    plans: list[SyncPlan] | None = None,
) -> SyncPlan:
    """Build one strict grouped synchronization plan for tests."""
    if plans is None and (signatures or paths or partitioned_tables):
        plans = [
            SyncPlan(
                schema_name=schema_name,
                signatures=signatures or {},
                paths=paths or {},
                partitioned_tables=partitioned_tables or {},
            )
        ]

    return SyncPlan(
        schema_name=schema_name,
        signatures=signatures or {},
        paths=paths or {},
        partitioned_tables=partitioned_tables or {},
    )


def dump(
    *,
    run_id: str = "r1",
    table: str = "p.d.t",
    bucket_path: str = "s3://b/t",
) -> DumpTask:
    """Build one common dump task for tests."""
    return DumpTask(
        run_id=run_id,
        table=table,
        bucket_path=bucket_path,
        selection=AllSelection(),
    )


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
    executed_many: list[tuple[object, list[object]]]

    def __init__(self) -> None:
        self.executed = []
        self.executed_many = []

    def execute(self, query: object, params: object = None) -> FakePgConn:
        self.executed.append(query)
        return self

    def executemany(self, query: object, params_seq: Iterable[object]) -> FakePgConn:
        """Record one batch execution."""
        self.executed_many.append((query, list(params_seq)))
        return self

    def cursor(self, *, binary: bool = False) -> FakePgConn:
        """Return this test double as a cursor."""
        return self

    def commit(self) -> None:
        """Record an implicit successful commit."""

    def transaction(self) -> contextlib.AbstractContextManager[None]:
        """Return a no-op transaction context manager."""
        return contextlib.nullcontext()

    def __enter__(self) -> FakePgConn:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


@final
class FakeRedis:
    """Async Redis double with counters and dictionary-backed values."""

    store: dict[str, str]
    set_calls: list[tuple[str, object, int | None]]
    xtrim_calls: list[tuple[str, str | None]]
    hashes: dict[str, dict[str, str]]
    watch_errors: int
    transaction_commands: list[str]
    conflict_store_updates: dict[str, str]
    pending_consumers: set[str]
    pending_groups: set[tuple[str, str]]
    cleanup_error: RedisError | None
    deleted_consumers: list[tuple[str, str, str]]

    def __init__(
        self,
        watch_errors: int = 0,
        conflict_store_updates: dict[str, str] | None = None,
        pending_consumers: set[str] | None = None,
        pending_groups: set[tuple[str, str]] | None = None,
        cleanup_error: RedisError | None = None,
    ) -> None:
        self.store = {}
        self.set_calls = []
        self.xtrim_calls = []
        self.hashes = {}
        self.watch_errors = watch_errors
        self.transaction_commands = []
        self.conflict_store_updates = conflict_store_updates or {}
        self.pending_consumers = pending_consumers or set()
        self.pending_groups = pending_groups or set()
        self.cleanup_error = cleanup_error
        self.deleted_consumers = []

    async def __aenter__(self) -> FakeRedis:
        """Enter this direct Redis test double."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit this direct Redis test double."""

    async def get(self, key: str) -> str | None:
        """Return one stored string value."""
        return self.store.get(key)

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
        return removed

    async def xrange(self, _name: str) -> list[object]:
        """Return retained stream entries."""
        return []

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hvals(self, key: str) -> Sequence[bytes | str]:
        """Return all values from one hash."""
        return list(self.hashes.get(key, {}).values())

    async def xpending_range(
        self,
        name: str,
        groupname: str,
        _minimum: str,
        _maximum: str,
        count: int,
        consumername: str | None = None,
    ) -> list[object]:
        """Return a pending marker for configured consumers or groups."""
        if self.cleanup_error is not None:
            raise self.cleanup_error
        if consumername in self.pending_consumers:
            return [object()]
        if (name, groupname) in self.pending_groups:
            return [object()]
        return []

    async def xgroup_delconsumer(
        self,
        name: str,
        groupname: str,
        consumername: str,
    ) -> int:
        """Record deletion of an idle stream consumer."""
        if self.cleanup_error is not None:
            raise self.cleanup_error
        self.deleted_consumers.append((name, groupname, consumername))
        return 1

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        """Return one optimistic transaction double."""
        return FakePipeline(self)

    async def expire(self, key: str, _seconds: int) -> bool:
        """Accept a TTL for an existing test key."""
        return key in self.store

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
class FakePipeline:
    """Minimal optimistic Valkey transaction double."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.commands: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def watch(self, *keys: str) -> None:
        """Accept watched keys for one transaction."""

    async def get(self, key: str) -> str | None:
        """Read one watched string value."""
        return self.redis.store.get(key)

    async def hexists(self, key: str, field: str) -> bool:
        """Return whether one watched hash field exists."""
        return field in self.redis.hashes.get(key, {})

    def multi(self) -> None:
        """Start queued transaction commands."""

    def hset(self, key: str, field: str, value: object) -> None:
        """Queue one hash result write."""
        self.commands.append(("hset", (key, field, value), {}))

    def hdel(self, key: str, field: str) -> None:
        """Queue removal of one hash field."""
        self.commands.append(("hdel", (key, field), {}))

    def hlen(self, key: str) -> None:
        """Queue one hash length result."""
        self.commands.append(("hlen", (key,), {}))

    def set(self, key: str, value: object, **options: object) -> None:
        """Queue one string write."""
        self.commands.append(("set", (key, value), options))

    def expire(self, key: str, seconds: int) -> None:
        """Queue one TTL refresh."""
        self.commands.append(("expire", (key, seconds), {}))

    def delete(self, *keys: str) -> None:
        """Queue removal of one or more temporary keys."""
        self.commands.append(("delete", keys, {}))

    async def execute(self) -> list[object]:
        """Apply queued commands or inject one watch conflict."""
        if self.redis.watch_errors:
            self.redis.watch_errors -= 1
            self.redis.store.update(self.redis.conflict_store_updates)
            raise WatchError

        results: list[object] = []
        self.redis.transaction_commands = [command for command, _, _ in self.commands]
        for command, args, options in self.commands:
            if command == "hset":
                key, field, value = args
                hash_values = self.redis.hashes.setdefault(str(key), {})
                hash_values[str(field)] = str(value)
                results.append(1)
            elif command == "set":
                key, value = args
                expiration = cast("int | None", options.get("ex"))
                self.redis.store[str(key)] = str(value)
                self.redis.set_calls.append((str(key), value, expiration))
                results.append(True)
            elif command == "hdel":
                key, field = args
                removed = self.redis.hashes.get(str(key), {}).pop(str(field), None)
                results.append(1 if removed is not None else 0)
            elif command == "hlen":
                key = args[0]
                results.append(len(self.redis.hashes.get(str(key), {})))
            elif command == "expire":
                results.append(True)
            elif command == "delete":
                removed = 0
                for key in args:
                    removed += self.redis.store.pop(str(key), None) is not None
                    removed += self.redis.hashes.pop(str(key), None) is not None
                results.append(removed)
        return results


@final
class FakeBigQueryClient:
    """Metadata client double exposing table and partition metadata."""

    _modified: datetime | None
    _range_partitioning: RangePartitioning | None
    _time_partitioning: TimePartitioning | None
    rows: list[dict[str, object]]
    _table_type: str
    _schema: list[SchemaField]
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
        self._range_partitioning = range_partitioning
        self._time_partitioning = time_partitioning
        self.rows = rows or []
        self._table_type = table_type
        self._schema = [SchemaField("value", "INTEGER")]
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
    def range_partitioning(self) -> RangePartitioning | None:
        """Return configured range partition metadata."""
        return self._range_partitioning

    @property
    def time_partitioning(self) -> TimePartitioning | None:
        """Return configured time partition metadata."""
        return self._time_partitioning

    @property
    def table_type(self) -> str:
        """Return configured table type."""
        return self._table_type

    @property
    def schema(self) -> Sequence[SchemaField]:
        """Return configured schema metadata."""
        return self._schema

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
