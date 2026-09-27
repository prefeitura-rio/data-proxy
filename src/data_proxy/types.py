"""Shared type aliases and protocols used across Data Proxy modules."""

from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from datetime import datetime
from typing import Literal, LiteralString, Protocol

from google.cloud.bigquery import QueryJobConfig
from psycopg.sql import Composable
from whenever import Instant

type DatabaseValue = bool | int | float | str | datetime | Instant | None
type DatabaseRow = tuple[object, ...]
type PostgresParams = tuple[DatabaseValue, ...] | dict[str, DatabaseValue]
type DuckDBValue = DatabaseValue | Sequence[str]
type DuckDBParams = Sequence[DuckDBValue]
type BigQueryParams = QueryJobConfig
type RunStatus = Literal["success", "no_changes", "failure"]
type StatusRecorder = Callable[[RunStatus], Awaitable[None]]
type SyncWorkflow[**P] = Callable[P, Awaitable[RunStatus]]
type ObservedWorkflow[**P] = Callable[P, Coroutine[object, object, None]]
type TemplateValue = (
    str
    | bool
    | Composable
    | Sequence[str]
    | Sequence[Composable]
    | Mapping[str, TemplateValue]
    | Sequence[Mapping[str, TemplateValue]]
)


class DatabaseConnection[Params, Rows](Protocol):
    """Protocol for SQL backends that execute statements and query rows."""

    async def execute(self, sql: LiteralString, *, params: Params | None = ...) -> None:
        """Run one SQL statement."""
        ...

    async def query(self, sql: LiteralString, *, params: Params | None = ...) -> Rows:
        """Run one SQL query and return all rows."""
        ...
