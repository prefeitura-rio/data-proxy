"""Test boundary types shared by service fixtures."""

from dataclasses import dataclass

from minio import Minio
from psycopg import AsyncConnection
from psycopg.sql import Identifier


@dataclass(frozen=True, slots=True)
class Postgres:
    """Async PostgreSQL test boundary for one isolated schema."""

    connection: AsyncConnection
    dsn: str
    namespace: PostgresTestNamespace


@dataclass(frozen=True, slots=True)
class SeaweedFS:
    """SeaweedFS endpoint and S3-compatible client."""

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
