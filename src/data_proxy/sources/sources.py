"""Registry of data sources DuckDB can read."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Self

SourceKind = Literal["local", "remote"]


@dataclass(frozen=True)
class Source:
    """One source DuckDB can read."""

    name: str
    kind: SourceKind
    load: str
    scan: Callable[[str], str]
    suffix: str
    arg: Literal["snapshot", "covered"]


@dataclass
class Sources:
    """Registry of sources, instantiated once and reused."""

    sources: dict[str, Source] = field(default_factory=dict)

    def register(self, sources: Source | list[Source]) -> Self:
        """Register one or more sources."""
        if isinstance(sources, Source):
            sources = [sources]

        for source in sources:
            self.sources[source.name] = source

        return self

    def get(self, name: str) -> Source:
        """Return one registered source or fail with a clear error."""
        if name not in self.sources:
            known = ", ".join(sorted(self.sources, key=lambda s: s))
            msg = f"unknown source: {name} (known sources: {known})"
            raise ValueError(msg)
        return self.sources[name]

    def all(self) -> list[Source]:
        """Return every registered source."""
        return list(self.sources.values())


sources = Sources().register(
    [
        Source(
            name="ducklake",
            kind="local",
            load="",
            scan=lambda table: f"dl.{table}",
            suffix="dl_fn",
            arg="snapshot",
        ),
        Source(
            name="bigquery",
            kind="remote",
            load="LOAD bigquery",
            scan=lambda table: f"bigquery_scan(''{table}'')",
            suffix="bq_fn",
            arg="covered",
        ),
    ]
)
