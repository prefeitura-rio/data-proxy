"""Test boundary types shared by service fixtures."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import final, override

from lightkube.core.exceptions import ConditionError
from lightkube.core.resource import NamespacedResource
from lightkube.models.apps_v1 import DeploymentSpec
from lightkube.models.core_v1 import PodTemplateSpec
from lightkube.models.meta_v1 import LabelSelector, ObjectMeta
from lightkube.operators import BinaryOperator, Operator, SequenceOperator
from lightkube.resources.apps_v1 import Deployment
from lightkube.resources.batch_v1 import Job
from lightkube.resources.core_v1 import Pod
from lightkube.types import CascadeType, PatchType
from minio import Minio
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier
from pydantic import JsonValue
from testcontainers.community.postgres import PostgresContainer

from data_proxy.postgres import Postgres as Pg
from data_proxy.types import KubernetesClient


@dataclass(frozen=True, slots=True)
class Postgres:
    """Async PostgreSQL test boundary for one isolated schema."""

    connection: AsyncConnection
    dsn: str
    namespace: PostgresTestNamespace

    @property
    def backend(self) -> Pg:
        """Return the application Postgres backend wrapping this connection."""
        return Pg(connection=self.connection)


@dataclass(frozen=True, slots=True)
class Silo:
    """Silo endpoint and S3-compatible client."""

    host: str
    port: int
    client: Minio


@dataclass(frozen=True, slots=True)
class PostgresTestNamespace:
    """One per-test PostgreSQL schema and its related identifiers."""

    schema: str

    @property
    def identifier(self) -> Identifier:
        """Return the safely quoted schema identifier."""
        return Identifier(self.schema)

    def table(self, name: str) -> Identifier:
        """Return a safely quoted table identifier in this namespace."""
        return Identifier(self.schema, name)

    def policy(self, name: str) -> Identifier:
        """Return a safely quoted policy identifier."""
        return Identifier(name)


@dataclass(frozen=True, slots=True)
class BigQueryQueryResult:
    """Concrete query job result for the BigQuery fixture."""

    rows: list[dict[str, str | int | None]]

    def result(self) -> list[dict[str, str | int | None]]:
        """Return fixture rows as the BigQuery query job does."""
        return self.rows


@final
@dataclass(frozen=True, slots=True)
class NoFallbackSource:
    """Source test double without PostgreSQL fallback support."""

    name: str = "no-fallback"
    fallback: None = None
    load: str = ""
    extensions: tuple[str, ...] = ()

    def with_settings(self, settings: dict[str, JsonValue] | None) -> NoFallbackSource:
        """Build a settings-free test source."""
        return NoFallbackSource()

    def validate(self, table: str) -> None:
        """Accept every test source reference."""

    def scan(self, table: str) -> SQL:
        """Return a safe test scan expression."""
        return SQL("no_fallback_scan")

    async def modified(self, table: str) -> str:
        """Return a stable modification value."""
        return "modified"

    async def close(self) -> None:
        """Release no resources."""


@final
@dataclass(frozen=True, slots=True)
class FullOnlySource:
    """Source test double without physical partition support."""

    name: str = "full-only"
    fallback: None = None
    load: str = ""
    extensions: tuple[str, ...] = ()

    def with_settings(self, settings: dict[str, JsonValue] | None) -> FullOnlySource:
        """Build a settings-free test source."""
        return FullOnlySource()

    def validate(self, table: str) -> None:
        """Accept every test table reference."""

    def scan(self, table: str) -> SQL:
        """Return a safe test scan expression."""
        return SQL("full_only_scan")

    async def modified(self, table: str) -> str:
        """Return a stable modification value."""
        return "modified"

    async def close(self) -> None:
        """Release no resources."""


@dataclass(slots=True)
class Calls:
    """Workflow steps in the order they run, with the outcomes they return."""

    snapshot_id: int | None = 42
    maintained_snapshot_id: int = 45
    maintenance_error: Exception | None = None
    names: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Psql:
    """psql in the PostgreSQL container, as the Helm jobs run it."""

    container: PostgresContainer
    database: str

    def run(self, script: str, variables: Mapping[str, str] | None = None) -> str:
        """Run one psql script, stop at the first error, and return its output."""
        settings = [
            f"--set={name}={value}" for name, value in (variables or {}).items()
        ]
        result = self.container.exec(
            [
                "sh",
                "-c",
                'printf "%s" "$0" | "$@"',
                script,
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--set=ON_ERROR_STOP=1",
                f"--username={self.container.username}",
                f"--dbname={self.database}",
                *settings,
            ]
        )
        output = result.output.decode()
        assert result.exit_code == 0, output
        return output


@final
class FakeKubernetes(KubernetesClient):
    """In-memory Kubernetes client that serves objects and records every call."""

    def __init__(
        self,
        objects: Iterable[Pod | Deployment | Job] = (),
        *,
        watched: Iterable[Deployment] = (),
        hang_watch: bool = False,
        wait_error: ConditionError | None = None,
    ) -> None:
        self.objects = list(objects)
        self.watched = list(watched)
        self.hang_watch = hang_watch
        self.watch_timeouts: list[int | None] = []
        self.wait_error = wait_error
        self.list_labels: list[
            dict[str, str | Operator[str] | Iterable[str] | None]
        ] = []
        self.patched: list[str] = []
        self.created: list[Job] = []
        self.waited: list[tuple[str, list[str], list[str]]] = []
        self.deleted: list[tuple[str, CascadeType]] = []

    @override
    def list[R: NamespacedResource](
        self,
        res: type[R],
        *,
        namespace: str,
        labels: dict[str, str | Operator[str] | Iterable[str] | None],
    ) -> AsyncIterable[R]:
        self.list_labels.append(labels)
        return self.serve(res)

    async def serve[R: NamespacedResource](self, res: type[R]) -> AsyncIterator[R]:
        for item in self.objects:
            if isinstance(item, res):
                yield item

    @override
    def watch(
        self,
        res: type[Deployment],
        *,
        namespace: str,
        fields: dict[str, str | BinaryOperator | SequenceOperator],
        server_timeout: int | None,
    ) -> AsyncIterable[tuple[str, Deployment]]:
        self.watch_timeouts.append(server_timeout)
        return self.stream()

    async def stream(self) -> AsyncIterator[tuple[str, Deployment]]:
        for item in self.watched:
            yield "MODIFIED", item

        if self.hang_watch:
            await asyncio.Event().wait()

    @override
    async def patch(
        self,
        res: type[Deployment],
        name: str,
        obj: dict[str, JsonValue],
        *,
        namespace: str,
        patch_type: PatchType,
    ) -> Deployment:
        self.patched.append(name)
        return Deployment(
            metadata=ObjectMeta(name=name),
            spec=DeploymentSpec(
                selector=LabelSelector(), template=PodTemplateSpec(), replicas=1
            ),
        )

    @override
    async def create(self, obj: Job) -> Job:
        self.created.append(obj)
        return obj

    @override
    async def wait(
        self,
        res: type[Job],
        name: str,
        *,
        namespace: str,
        for_conditions: Iterable[str],
        raise_for_conditions: Iterable[str],
    ) -> Job:
        self.waited.append((name, list(for_conditions), list(raise_for_conditions)))

        if self.wait_error is not None:
            raise self.wait_error

        return Job(metadata=ObjectMeta(name=name))

    @override
    async def delete(
        self,
        res: type[Job],
        name: str,
        *,
        namespace: str,
        cascade: CascadeType,
    ) -> None:
        self.deleted.append((name, cascade))
