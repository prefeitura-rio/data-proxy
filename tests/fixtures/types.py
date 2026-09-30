"""Test boundary types shared by service fixtures."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from minio import Minio
from psycopg import AsyncConnection
from psycopg.sql import Identifier
from testcontainers.community.postgres import PostgresContainer

from data_proxy.postgres import Postgres as Pg


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


@dataclass(slots=True)
class Calls:
    """Workflow steps in the order they run, with the outcomes they return."""

    snapshot_id: int | None = 42
    wait_error: Exception | None = None
    names: list[str] = field(default_factory=list)
    waited: list[tuple[str, int]] = field(default_factory=list)


@dataclass(slots=True)
class FakeReader:
    """Reader snapshots returned one per poll, with a clock driven by sleeps."""

    snapshots: list[int | None] = field(default_factory=list)
    polls: int = 0
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    async def read(self) -> int | None:
        """Return the next snapshot and repeat the last one after the end."""
        index = min(self.polls, len(self.snapshots) - 1)
        self.polls += 1
        return self.snapshots[index]

    async def sleep(self, seconds: float) -> None:
        """Advance the clock instead of sleeping."""
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        """Return the simulated time."""
        return self.now


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
