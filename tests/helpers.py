"""Typed test doubles for the data-proxy test suite."""

from typing import final


@final
class FakeDuckDBConnection:
    """In-memory DuckDB connection double with call tracking."""

    _rows: list[tuple[object, ...]]
    executed: list[str]

    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []
        self.executed = []

    def execute(self, query: str, parameters: object = None) -> FakeDuckDBConnection:
        self.executed.append(query)
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def __enter__(self) -> FakeDuckDBConnection:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        pass


@final
class FakePgConn:
    """Minimal psycopg connection double with execute call tracking."""

    execute_calls: int

    def __init__(self) -> None:
        self.execute_calls = 0

    def execute(self, query: object, params: object = None) -> None:
        self.execute_calls += 1


@final
class FakeRedis:
    """Async Redis double with configurable decr return value."""

    _decr_value: int

    def __init__(self, decr_value: int = 1) -> None:
        self._decr_value = decr_value

    async def decr(self, key: str) -> int:
        return self._decr_value

    async def set(self, key: str, value: object, ex: int | None = None) -> None:
        pass


@final
class FakeRedisCM:
    """Async context manager wrapping a FakeRedis."""

    _redis: FakeRedis

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis

    async def __aenter__(self) -> FakeRedis:
        return self._redis

    async def __aexit__(self, *args: object) -> None:
        pass
