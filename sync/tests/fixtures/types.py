import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from os import environ
from typing import TypedDict, final

from minio import Minio
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier
from pydantic import JsonValue

from sync.postgres import Postgres as Pg
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
class Storage:
    """Object storage endpoint and S3-compatible client."""

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
        chosen: Mapping[str, str] = {} if variables is None else variables
        settings = [f"--set={name}={value}" for name, value in chosen.items()]
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


class FilterCase(TypedDict):
    """One case of the shared fixture that the Lua parser specs and the filter tests read."""

    name: str
    expected: dict[str, JsonValue] | None
