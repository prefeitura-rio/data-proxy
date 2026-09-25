"""Data models for the sync service."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, ClassVar, Literal, Self, override

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    computed_field,
    model_validator,
)

from .constants import BIGQUERY_TABLE_REFERENCE_PATTERN
from .types import JsonValue

NonEmptyString = Annotated[str, Field(min_length=1)]
BigQueryTableName = Annotated[
    NonEmptyString, Field(pattern=BIGQUERY_TABLE_REFERENCE_PATTERN)
]


class Strategy(StrEnum):
    """Table synchronization strategy: whole-table or physically partitioned."""

    FULL = "full"
    PARTITIONED = "partitioned"


class UnitMapping(BaseModel):
    """One row column that identifies membership in a unit of the given type."""

    column: NonEmptyString
    unit_type: NonEmptyString


class IndexConfig(BaseModel):
    """Index definition for a synced table."""

    name: NonEmptyString
    columns: Annotated[list[NonEmptyString], Field(min_length=1)]


class AllSelection(BaseModel):
    """Select every row from a source table."""

    type: Literal["all"] = "all"

    def check_id(self, partition_id: str) -> None:
        """No partition ID validation for a full-table selection."""


class TimeRangeSelection(BaseModel):
    """Select rows within one time partition's [lower, upper) date/timestamp bounds."""

    type: Literal["time_range"] = "time_range"
    column: NonEmptyString
    lower: NonEmptyString
    upper: NonEmptyString

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Require chronological lower and upper bounds."""
        if self.lower >= self.upper:
            raise ValueError("Time selection lower bound must precede upper bound")
        return self

    def check_id(self, partition_id: str) -> None:
        """No partition ID validation for a time-range selection."""


class RangeSelection(BaseModel):
    """Select rows within one physical integer partition."""

    type: Literal["range"] = "range"
    partition_id: NonEmptyString
    column: NonEmptyString
    lower: int
    upper: int

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Require a non-empty integer range."""
        if self.lower >= self.upper:
            raise ValueError("Range selection lower bound must precede upper bound")
        return self

    def check_id(self, partition_id: str) -> None:
        """Require the selection ID to match the physical partition."""
        if self.partition_id != partition_id:
            raise ValueError(
                "Range selection partition ID must match physical partition"
            )


class RemainderSelection(BaseModel):
    """Select rows in BigQuery's ``__NULL__`` bucket: null or out-of-range values."""

    type: Literal["remainder"] = "remainder"
    column: NonEmptyString
    start: int
    end: int

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Require a non-empty remainder range."""
        if self.start >= self.end:
            raise ValueError("Remainder selection start must precede end")
        return self

    def check_id(self, partition_id: str) -> None:
        """No partition ID validation for a remainder selection."""


TaskSelection = Annotated[
    AllSelection | TimeRangeSelection | RangeSelection | RemainderSelection,
    Field(discriminator="type"),
]


class PhysicalPartition(BaseModel):
    """Normalized state and extraction selection for one physical BigQuery partition."""

    partition_id: NonEmptyString
    signature: NonEmptyString
    selection: TimeRangeSelection | RangeSelection | RemainderSelection
    logical_bytes: int = 0
    """Uncompressed source size, used to group partitions into extraction batches."""

    @model_validator(mode="after")
    def validate_range_partition_id(self) -> Self:
        """Require range selection IDs to match their physical partition."""
        self.selection.check_id(self.partition_id)
        return self


class Table(BaseModel):
    """Common configuration shared by every synced table strategy."""

    name: BigQueryTableName
    rls: list[UnitMapping] | None = None
    indexes: list[IndexConfig] = []
    fallback: bool = False
    cache_ttl: int | None = None
    """Lifetime of a proxy cache entry for this table, in seconds."""
    resolved_schema: str = ""
    """The schema this table is nested under. Stamped by SyncConfig, never user input."""

    @property
    def table_name(self) -> str:
        """Return the unqualified source table name."""
        return self.name.split(".")[-1]

    def config_signature_fields(self) -> dict[str, JsonValue]:
        """Return the configuration fields that identify this table for a sync."""
        return {
            "name": self.name,
            "rls": [r.model_dump() for r in self.rls] if self.rls else None,
            "indexes": [i.model_dump() for i in self.indexes] if self.indexes else None,
        }

    def to_task(
        self,
        run_id: str,
        s3_bucket: str,
        scratch_prefix: str,
        selections: list[TaskSelection],
        path_suffix: str | None = None,
        json_columns: list[str] | None = None,
    ) -> DumpTask:
        """Create one extraction task for the selected source rows."""
        suffix = f"/{path_suffix}" if path_suffix else ""

        prefix = f"s3://{s3_bucket}/{scratch_prefix}/"
        return DumpTask(
            run_id=run_id,
            table=self.name,
            target_schema=self.resolved_schema,
            bucket_path=(
                prefix
                + f"{self.resolved_schema}/{self.table_name}{suffix}/data.parquet"
            ),
            selections=selections,
            json_columns=json_columns or [],
        )


class FullTable(Table):
    """A table synced by replacing it wholesale on every run."""

    strategy: Literal[Strategy.FULL] = Strategy.FULL

    @override
    def config_signature_fields(self) -> dict[str, JsonValue]:
        """Include the strategy and a null partition window for a full table."""
        fields = super().config_signature_fields()
        fields["strategy"] = self.strategy
        fields["n"] = None
        return fields


class PartitionedTable(Table):
    """A table synced by diffing and reloading only its changed physical partitions."""

    strategy: Literal[Strategy.PARTITIONED] = Strategy.PARTITIONED
    n: PositiveInt | None = None
    """Keep only the last N time partitions. Time-partitioned tables only."""

    @override
    def config_signature_fields(self) -> dict[str, JsonValue]:
        """Include the strategy and the partition window."""
        fields = super().config_signature_fields()
        fields["strategy"] = self.strategy
        fields["n"] = self.n
        return fields


TableConfig = Annotated[
    FullTable | PartitionedTable,
    Field(discriminator="strategy"),
]


class SchemaConfig(BaseModel):
    """A PostgreSQL schema: its tables and, if any use RLS, its access claim."""

    claim: NonEmptyString | None = None
    tables: list[TableConfig] = []


class SyncConfig(BaseModel):
    """The full set of schemas and their nested tables a synchronization run manages."""

    schemas: dict[str, SchemaConfig] = {}

    @property
    def tables(self) -> list[TableConfig]:
        """Return every table across every schema."""
        return [table for schema in self.schemas.values() for table in schema.tables]

    @model_validator(mode="after")
    def stamp_resolved_schema(self) -> Self:
        """Assign each table's schema from the key it's nested under."""
        for name, schema in self.schemas.items():
            for table in schema.tables:
                table.resolved_schema = name

        return self

    @model_validator(mode="after")
    def reject_duplicate_table_names(self) -> Self:
        """Require every configured source table to have one destination."""
        names = [table.name for table in self.tables]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Duplicate configured table names: {duplicates}")
        return self

    @model_validator(mode="after")
    def require_claim_for_rls(self) -> Self:
        """Decline rls tables nested under a schema with no claim."""
        for name, schema in self.schemas.items():
            if schema.claim is not None:
                continue

            offenders = [table.name for table in schema.tables if table.rls]

            if offenders:
                message = (
                    f"Schema {name!r} has no claim but rls tables: {sorted(offenders)}"
                )
                raise ValueError(message)

        return self


class DumpTask(BaseModel):
    """One extraction unit and its scratch Parquet destinations."""

    run_id: str
    table: str
    target_schema: str
    bucket_path: str
    selections: Annotated[list[TaskSelection], Field(min_length=1)]
    json_columns: list[str] = []

    @computed_field
    @property
    def output_paths(self) -> list[str]:
        """Return one scratch Parquet path for each extraction selection."""
        if len(self.selections) == 1:
            return [self.bucket_path]

        prefix, _, filename = self.bucket_path.rpartition("/")
        stem = filename.removesuffix(".parquet")
        return [
            f"{prefix}/{stem}-{index}.parquet" for index in range(len(self.selections))
        ]

    @computed_field
    @property
    def task_id(self) -> str:
        """Return the deterministic identity for this run and task path."""
        return sha256(f"{self.run_id}:{self.bucket_path}".encode()).hexdigest()


class DumpStatus(StrEnum):
    """Result status for one extraction task."""

    SUCCESS = "success"
    FAILURE = "error"


class DumpSuccess(BaseModel):
    """Successful extraction task result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    status: Literal[DumpStatus.SUCCESS] = DumpStatus.SUCCESS
    failed_paths: list[str] = []


class DumpFailure(BaseModel):
    """Failed extraction task result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    status: Literal[DumpStatus.FAILURE] = DumpStatus.FAILURE
    failed_paths: list[str]


DumpResult = Annotated[DumpSuccess | DumpFailure, Field(discriminator="status")]


class PartitionedTablePlan(BaseModel):
    """Current and affected physical partitions for one table."""

    table_signature: str
    full_rebuild: bool
    current_partitions: dict[str, PhysicalPartition]
    changed_paths: dict[str, str]
    previous_partitions: dict[str, PhysicalPartition] = {}
    removed_partitions: dict[str, PhysicalPartition]

    @model_validator(mode="after")
    def validate_partition_sets(self) -> Self:
        """Require changed and removed IDs to match their respective manifests."""
        if not self.changed_paths.keys() <= self.current_partitions.keys():
            msg = "Changed partition paths must exist in the current manifest"
            raise ValueError(msg)

        if not self.previous_partitions.keys() <= self.changed_paths.keys():
            msg = "Previous partitions must be changed partitions"
            raise ValueError(msg)

        if self.removed_partitions.keys() & self.current_partitions.keys():
            msg = "Removed partitions can't exist in the current manifest"
            raise ValueError(msg)
        return self


class PartitionManifest(BaseModel):
    """Committed physical partition state for one source table."""

    table_signature: str
    partitions: dict[str, PhysicalPartition]


class SyncPlan(BaseModel):
    """Immutable publication inputs for one PostgreSQL schema."""

    schema_name: str
    signatures: dict[str, str] = {}
    paths: dict[str, list[str]] = {}
    partitioned_tables: dict[str, PartitionedTablePlan] = {}

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        """Require ordinary signatures and non-empty paths to match."""
        if self.signatures.keys() != self.paths.keys() or any(
            not paths for paths in self.paths.values()
        ):
            raise ValueError("Sync plan signatures and non-empty paths must match")
        if self.signatures.keys() & self.partitioned_tables.keys():
            raise ValueError("Tables can't have ordinary and partitioned plans")
        return self


class TableState(BaseModel):
    """Committed state for one table."""

    strategy: Strategy
    signature: str
    partitions: dict[str, PhysicalPartition] | None = None


@dataclass(frozen=True, slots=True)
class SyncWork:
    """Producer planning result."""

    plans: list[SyncPlan]
    tasks: list[DumpTask]


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    """Publishable plan and failures derived from extraction results."""

    plan: SyncPlan
    blocked_tables: set[str]
    failed_partitions: dict[str, set[str]]


class PublicationResult(BaseModel):
    """Exact plan and table set published by the publisher."""

    plan: SyncPlan
    published_tables: set[str]
