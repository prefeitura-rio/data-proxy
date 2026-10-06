"""Shared type aliases and protocols used across Data Proxy modules."""

from collections.abc import (
    AsyncIterable,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import Annotated, Literal, LiteralString, Protocol

from lightkube.core.resource import NamespacedResource
from lightkube.operators import BinaryOperator, Operator, SequenceOperator
from lightkube.resources.apps_v1 import Deployment
from lightkube.resources.batch_v1 import Job
from lightkube.types import CascadeType, PatchType
from psycopg.sql import Composable
from pydantic import Field, JsonValue
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


class KubernetesClient(Protocol):
    """The part of the Lightkube client the Kubernetes helpers use, with fully known types."""

    def list[R: NamespacedResource](
        self,
        res: type[R],
        *,
        namespace: str,
        labels: dict[str, str | Operator[str] | Iterable[str] | None],
    ) -> AsyncIterable[R]: ...

    def watch(
        self,
        res: type[Deployment],
        *,
        namespace: str,
        fields: dict[str, str | BinaryOperator | SequenceOperator],
        server_timeout: int | None,
    ) -> AsyncIterable[tuple[str, Deployment]]: ...

    async def patch(
        self,
        res: type[Deployment],
        name: str,
        obj: dict[str, JsonValue],
        *,
        namespace: str,
        patch_type: PatchType,
    ) -> Deployment: ...

    async def create(self, obj: Job) -> Job: ...

    async def wait(
        self,
        res: type[Job],
        name: str,
        *,
        namespace: str,
        for_conditions: Iterable[str],
        raise_for_conditions: Iterable[str],
    ) -> Job: ...

    async def delete(
        self,
        res: type[Job],
        name: str,
        *,
        namespace: str,
        cascade: CascadeType,
    ) -> None: ...
