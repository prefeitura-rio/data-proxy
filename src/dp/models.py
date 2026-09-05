"""Data models for the sync pipeline."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    TypeAdapter,
    computed_field,
    model_validator,
)

from .constants import BIGQUERY_TABLE_REFERENCE_PATTERN

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
    method: Literal["btree", "gin"] = "btree"
    expressions: list[NonEmptyString] | None = None


class AllSelection(BaseModel):
    """Select every row from a source table."""

    type: Literal["all"] = "all"


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


TaskSelection = Annotated[
    AllSelection | TimeRangeSelection | RangeSelection | RemainderSelection,
    Field(discriminator="type"),
]


class PhysicalPartition(BaseModel):
    """Normalized state and extraction selection for one physical BigQuery partition."""

    partition_id: NonEmptyString
    signature: NonEmptyString
    selection: TimeRangeSelection | RangeSelection | RemainderSelection

    @model_validator(mode="after")
    def validate_range_partition_id(self) -> Self:
        """Require range selection IDs to match their physical partition."""
        if isinstance(self.selection, RangeSelection) and (
            self.partition_id != self.selection.partition_id
        ):
            raise ValueError(
                "Range selection partition ID must match physical partition"
            )
        return self


class Table(BaseModel):
    """Common configuration shared by every synced table strategy."""

    name: BigQueryTableName
    rls: list[UnitMapping] | None = None
    indexes: list[IndexConfig] = []
    resolved_schema: str = ""
    """The schema this table is nested under. Stamped by SyncConfig, never user input."""

    @property
    def table_name(self) -> str:
        """Return the unqualified source table name."""
        return self.name.split(".")[-1]

    def to_task(
        self,
        run_id: str,
        gcs_bucket: str,
        selection: TaskSelection,
        path_suffix: str | None = None,
        json_columns: list[str] | None = None,
    ) -> DumpTask:
        """Create one extraction task for the selected source rows."""
        suffix = f"/{path_suffix}" if path_suffix else ""

        return DumpTask(
            run_id=run_id,
            table=self.name,
            bucket_path=(
                f"s3://{gcs_bucket}/{self.resolved_schema}/"
                f"{self.table_name}{suffix}/data.parquet"
            ),
            selection=selection,
            json_columns=json_columns or [],
        )


class FullTable(Table):
    """A table synced by replacing it wholesale on every run."""

    strategy: Literal[Strategy.FULL] = Strategy.FULL


class PartitionedTable(Table):
    """A table synced by diffing and reloading only its changed physical partitions."""

    strategy: Literal[Strategy.PARTITIONED] = Strategy.PARTITIONED
    n: PositiveInt | None = None
    """Keep only the last N time partitions. Time-partitioned tables only."""


TableConfig = Annotated[
    FullTable | PartitionedTable,
    Field(discriminator="strategy"),
]


class SchemaWriters(BaseModel):
    """Mapping from PostgreSQL schema names to writer DSNs."""

    writers: dict[str, str]

    def dsn(self, schema: str) -> str:
        """Return the required writer DSN for a configured schema."""
        try:
            return self.writers[schema]
        except KeyError as error:
            raise RuntimeError(
                f"Writer DSN is not configured for schema {schema!r}"
            ) from error


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
        """Assign each table's schema from the key it is nested under."""
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
    def reserve_freshness_table(self) -> Self:
        """Reject source tables that conflict with freshness metadata."""
        offenders = [
            table.name for table in self.tables if table.table_name == "freshness"
        ]

        if offenders:
            message = f"Table name 'freshness' is reserved: {sorted(offenders)}"
            raise ValueError(message)

        return self

    @model_validator(mode="after")
    def require_claim_for_rls(self) -> Self:
        """Reject rls tables nested under a schema with no claim."""
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
    """One extraction unit: a source table (or partition) and its GCS destination."""

    run_id: str
    table: str
    bucket_path: str
    selection: TaskSelection
    json_columns: list[str] = []
    retry_count: int = 0

    @computed_field
    @property
    def task_id(self) -> str:
        """Return the deterministic identity for this run and task path."""
        return sha256(f"{self.run_id}:{self.bucket_path}".encode()).hexdigest()


class DumpStatus(StrEnum):
    """Result status for one extraction task."""

    SUCCESS = "success"
    FAILURE = "failure"


class DumpSuccess(BaseModel):
    """Successful extraction task result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    status: Literal[DumpStatus.SUCCESS] = DumpStatus.SUCCESS


class DumpFailure(BaseModel):
    """Failed extraction task result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    status: Literal[DumpStatus.FAILURE] = DumpStatus.FAILURE
    failed_path: str


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
            msg = "Removed partitions cannot exist in the current manifest"
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
            raise ValueError("Tables cannot have ordinary and partitioned plans")
        return self


class SeedTask(BaseModel):
    """Request shared database preparation for one run."""

    run_id: str


class PublishTask(BaseModel):
    """Request publication of one schema for one run."""

    run_id: str
    schema_name: str


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


class SyncPublicationInput(BaseModel):
    """A configuration and schema-local plan validated together."""

    config: SyncConfig
    plan: SyncPlan

    @property
    def changed_tables(self) -> set[str]:
        """Return every table with work in the plan."""
        return self.plan.signatures.keys() | self.plan.partitioned_tables.keys()

    @model_validator(mode="after")
    def require_configured_plan_tables(self) -> Self:
        """Reject a plan that names tables absent from its configuration."""
        unknown = self.changed_tables - {table.name for table in self.config.tables}
        if unknown:
            raise ValueError(f"Sync plan contains unknown tables: {sorted(unknown)}")
        return self


class PublicationDecision(BaseModel):
    """Publishable plan and failures derived from extraction results."""

    plan: SyncPlan
    blocked_tables: set[str]
    failed_partitions: dict[str, set[str]]


class PublicationResult(BaseModel):
    """Exact plan and table set published by the publisher."""

    plan: SyncPlan
    published_tables: set[str]


task_outcome_adapter: TypeAdapter[DumpResult] = TypeAdapter(DumpResult)
