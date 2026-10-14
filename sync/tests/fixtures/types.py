import asyncio
import subprocess
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from os import environ
from typing import final, override

from lightkube.core.resource import NamespacedResource
from lightkube.models.apps_v1 import DeploymentSpec
from lightkube.models.core_v1 import PodTemplateSpec
from lightkube.models.meta_v1 import LabelSelector, ObjectMeta
from lightkube.operators import BinaryOperator, Operator, SequenceOperator
from lightkube.resources.apps_v1 import Deployment
from lightkube.types import PatchType
from minio import Minio
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier
from pydantic import JsonValue

from sync.postgres import Postgres as Pg
from sync.types import KubernetesClient
from tests.constants import POSTGRES_HOST, POSTGRES_PORT


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
class Postgrest:
    """HTTP endpoint for the PostgREST test service."""

    url: str


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
    """psql against the PostgreSQL test server, as the Helm jobs run it."""

    database: str

    def run(self, script: str, variables: Mapping[str, str] | None = None) -> str:
        """Run one psql script, stop at the first error, and return its output."""
        settings = [
            f"--set={name}={value}" for name, value in (variables or {}).items()
        ]
        result = subprocess.run(
            [
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--set=ON_ERROR_STOP=1",
                f"--host={POSTGRES_HOST}",
                f"--port={POSTGRES_PORT}",
                "--username=test",
                f"--dbname={self.database}",
                *settings,
            ],
            input=script,
            capture_output=True,
            text=True,
            env={**environ, "PGPASSWORD": "test"},
            check=False,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        return output


@final
class FakeKubernetes(KubernetesClient):
    """In-memory Kubernetes client that serves objects and records every call."""

    def __init__(
        self,
        objects: Iterable[Deployment] = (),
        *,
        watched: Iterable[Deployment] = (),
        hang_watch: bool = False,
    ) -> None:
        self.objects = list(objects)
        self.watched = list(watched)
        self.hang_watch = hang_watch
        self.watch_timeouts: list[int | None] = []
        self.list_labels: list[
            dict[str, str | Operator[str] | Iterable[str] | None]
        ] = []
        self.patched: list[str] = []

    @override
    def list[R: NamespacedResource](
        self,
        res: type[R],
        *,
        namespace: str,
        labels: dict[str, str | Operator[str] | Iterable[str] | None],
    ) -> AsyncIterable[R]:
        """Record the label selector and serve the stored objects."""
        self.list_labels.append(labels)
        return self.serve(res)

    async def serve[R: NamespacedResource](self, res: type[R]) -> AsyncIterator[R]:
        """Yield every stored object of the given kind."""
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
        """Record the timeout and stream the stored watch events."""
        self.watch_timeouts.append(server_timeout)
        return self.stream()

    async def stream(self) -> AsyncIterator[tuple[str, Deployment]]:
        """Yield each watched deployment, and hang if the scenario asks."""
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
        """Record the patch and return a minimal deployment."""
        self.patched.append(name)
        return Deployment(
            metadata=ObjectMeta(name=name),
            spec=DeploymentSpec(
                selector=LabelSelector(), template=PodTemplateSpec(), replicas=1
            ),
        )
