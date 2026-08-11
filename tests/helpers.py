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

    def __init__(self, decr_value: int = 1, lag: int = 1) -> None:
        self._decr_value = decr_value
        self._lag = lag

    async def decr(self, key: str) -> int:
        return self._decr_value

    async def set(self, key: str, value: object, ex: int | None = None) -> None:
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
