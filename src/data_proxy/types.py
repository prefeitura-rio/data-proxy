"""Shared type aliases and protocols used across Data Proxy modules."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
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
type StatusRecorder = Callable[[Literal["success", "failure"]], Awaitable[None]]
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
