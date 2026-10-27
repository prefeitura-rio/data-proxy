"""Shared type aliases and protocols used across Data Proxy modules."""

from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import Annotated, Literal, LiteralString, Protocol

from psycopg.sql import Composable
from pydantic import Field
from whenever import Instant

type DatabaseValue = bool | int | float | str | datetime | Instant | None
type DatabaseRow = tuple[object, ...]
type PostgresParams = tuple[DatabaseValue, ...] | dict[str, DatabaseValue]
type DuckDBValue = DatabaseValue | Sequence[str]
type DuckDBParams = Sequence[DuckDBValue]
type NonEmptyString = Annotated[str, Field(min_length=1)]
type RunStatus = Literal["success", "failure"]
type StatusRecorder = Callable[[RunStatus], Awaitable[None]]
type TemplateValue = (
    str | bool | Composable | Sequence[TemplateValue] | Mapping[str, TemplateValue]
)


class DatabaseConnection[Params, Rows](Protocol):
    """Protocol for SQL backends that execute statements and query rows."""

    async def execute(self, sql: LiteralString, *, params: Params | None = ...) -> None:
        """Run one SQL statement."""
        ...

    async def query(self, sql: LiteralString, *, params: Params | None = ...) -> Rows:
        """Run one SQL query and return all rows."""
        ...
