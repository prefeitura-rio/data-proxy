"""Contracts and registry for external synchronization sources."""

from dataclasses import dataclass, field
from typing import Protocol, Self, runtime_checkable

from psycopg.sql import Composable
from pydantic import JsonValue

from .partitions import PartitionRequest, PhysicalPartition


class Fallback(Protocol):
    """PostgreSQL fallback metadata for one external source."""

    @property
    def suffix(self) -> str:
        """Return the private helper function suffix."""
        ...

    @property
    def template(self) -> str:
        """Return the fallback helper template path."""
        ...


class Source(Protocol):
    """One configured external synchronization source."""

    name: str
    fallback: Fallback | None
    load: str
    extensions: tuple[str, ...]

    def with_settings(self, settings: dict[str, JsonValue] | None) -> Self:
        """Return a fresh source configured with non-secret settings."""
        ...

    def validate(self, table: str) -> None:
        """Reject a table reference that this source cannot read."""
        ...

    def scan(self, table: str) -> Composable:
        """Return a SQL-safe DuckDB FROM expression for a valid table reference."""
        ...

    async def modified(self, table: str) -> str:
        """Return a stable modification value for one source table."""
        ...

    async def close(self) -> None:
        """Release every client held by this source."""
        ...


@runtime_checkable
class PartitionedSource(Source, Protocol):
    """A source that supports physical partition synchronization."""

    async def partitions(
        self, request: PartitionRequest
    ) -> tuple[str, dict[str, PhysicalPartition]]:
        """Return the current partition signature and physical partitions."""
        ...


@dataclass
class Sources:
    """Registry of external source dataclass instances."""

    sources: dict[str, Source] = field(default_factory=dict)

    def register(self, sources: list[Source]) -> Self:
        """Register source instances by their configured names."""
        for source in sources:
            if source.name in self.sources:
                raise ValueError(f"source already registered: {source.name}")
            self.sources[source.name] = source
        return self

    def all(self) -> list[Source]:
        """Return every registered source instance."""
        return list(self.sources.values())

    def configure(self, name: str, settings: dict[str, JsonValue] | None) -> Source:
        """Return a fresh configured source or fail with a clear error."""
        source = self.sources.get(name)

        if source is None:
            known = ", ".join(sorted(self.sources))
            raise ValueError(f"unknown source: {name} (known sources: {known})")

        return source.with_settings(settings)
