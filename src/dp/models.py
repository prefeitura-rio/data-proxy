"""Data models for the sync pipeline."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator


class Strategy(StrEnum):
    """Table synchronization strategy: whole-table or physically partitioned."""

    FULL = "full"
    PARTITIONED = "partitioned"


class UnitMapping(BaseModel):
    """One row column that identifies membership in a unit of the given type."""

    column: str
    unit_type: str


class IndexConfig(BaseModel):
    """Index definition for a synced table."""

    name: str
    columns: list[str]


class AllSelection(BaseModel):
    """Select every row from a source table."""

    type: Literal["all"] = "all"


class TimeRangeSelection(BaseModel):
    """Select rows within one time partition's [lower, upper) date/timestamp bounds."""

    type: Literal["time_range"] = "time_range"
    column: str
    lower: str
    upper: str


class RangeSelection(BaseModel):
    """Select rows within one physical integer partition."""

    type: Literal["range"] = "range"
    partition_id: str
    column: str
    lower: int
    upper: int


class RemainderSelection(BaseModel):
    """Select rows in BigQuery's ``__NULL__`` bucket: null or out-of-range values."""

    type: Literal["remainder"] = "remainder"
    column: str
    start: int
    end: int


TaskSelection = Annotated[
    AllSelection | TimeRangeSelection | RangeSelection | RemainderSelection,
    Field(discriminator="type"),
]


class PhysicalPartition(BaseModel):
    """Normalized state and extraction selection for one physical BigQuery partition."""

    partition_id: str
    signature: str
    selection: TimeRangeSelection | RangeSelection | RemainderSelection


class Table(BaseModel):
    """Common configuration shared by every synced table strategy."""

    name: str
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
        sync_id: str,
        gcs_bucket: str,
        selection: TaskSelection,
        path_suffix: str | None = None,
        json_columns: list[str] | None = None,
    ) -> SyncTask:
        """Create one extraction task for the selected source rows."""
        suffix = f"/{path_suffix}" if path_suffix else ""
        return SyncTask(
            sync_id=sync_id,
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
    n: int | None = None
    """Keep only the last N time partitions. Time-partitioned tables only."""


TableConfig = Annotated[
    FullTable | PartitionedTable,
    Field(discriminator="strategy"),
]


class SchemaConfig(BaseModel):
    """A PostgreSQL schema: its tables and, if any use RLS, its access claim."""

    claim: str | None = None
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


class SyncTask(BaseModel):
    """One extraction unit: a source table (or partition) and its GCS destination."""

    sync_id: str
    table: str
    bucket_path: str
    selection: TaskSelection
    json_columns: list[str] = []


class PartitionedTablePlan(BaseModel):
    """Current and affected physical partitions for one table."""

    table_signature: str
    full_rebuild: bool
    """Whether every current partition must be reloaded.

    Set on first sync or when the table-level signature changes (schema,
    RLS, indexes, or BigQuery schema drift), instead of diffing field-by-field.
    """
    current_partitions: dict[str, PhysicalPartition]
    changed_paths: dict[str, str]
    previous_partitions: dict[str, PhysicalPartition] = {}
    """Prior state for changed partitions that already exist."""
    removed_partitions: dict[str, PhysicalPartition]

    @model_validator(mode="after")
    def validate_partition_sets(self) -> Self:
        """Require changed and removed IDs to match their respective manifests."""
        if not set(self.changed_paths) <= set(self.current_partitions):
            msg = "Changed partition paths must exist in the current manifest"
            raise ValueError(msg)

        if not set(self.previous_partitions) <= set(self.changed_paths):
            msg = "Previous partitions must be changed partitions"
            raise ValueError(msg)

        if set(self.removed_partitions) & set(self.current_partitions):
            msg = "Removed partitions cannot exist in the current manifest"
            raise ValueError(msg)
        return self


class PartitionManifest(BaseModel):
    """Committed physical partition state for one source table."""

    table_signature: str
    partitions: dict[str, PhysicalPartition]


class SyncPlan(BaseModel):
    """Ordinary and partitioned table work for one synchronization run."""

    sync_id: str
    signatures: dict[str, str] = {}
    paths: dict[str, list[str]] = {}
    partitioned_tables: dict[str, PartitionedTablePlan] = {}

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        """Require ordinary signatures and non-empty paths to match."""
        if set(self.signatures) != set(self.paths) or any(
            not paths for paths in self.paths.values()
        ):
            message = "Sync plan signatures and non-empty paths must match"
            raise ValueError(message)

        if set(self.signatures) & set(self.partitioned_tables):
            message = "Tables cannot have ordinary and partitioned plans"
            raise ValueError(message)
        return self


class PublicationDecision(BaseModel):
    """Publishable plan and failures derived from extraction results."""

    plan: SyncPlan
    blocked_tables: set[str]
    failed_partitions: dict[str, set[str]]


class PublicationResult(BaseModel):
    """Exact plan and table set published by the finalizer."""

    plan: SyncPlan
    published_tables: set[str]


class FinalizeMessage(BaseModel):
    """Signal that every task for a sync run has completed."""

    sync_id: str


class ShutdownMessage(BaseModel):
    """Broadcast telling every worker to exit once finalization starts."""

    sync_id: str
