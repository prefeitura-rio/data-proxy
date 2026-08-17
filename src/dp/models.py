"""Data models for the sync pipeline."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator


class Strategy(StrEnum):
    ALL = "all"
    ALL_WITH_PARTITIONS = "all_with_partitions"
    WINDOW = "window"


class PartitionConfig(BaseModel):
    column: str
    n: int


class RlsConfig(BaseModel):
    """Row-level security configuration for a synced table."""

    column: str


class IndexConfig(BaseModel):
    """Index definition for a synced table."""

    name: str
    columns: list[str]


class AllSelection(BaseModel):
    """Select every row from a source table."""

    type: Literal["all"] = "all"


class ValueSelection(BaseModel):
    """Select rows equal to one logical window value."""

    type: Literal["value"] = "value"
    column: str
    value: str


class RangeSelection(BaseModel):
    """Select rows within one physical integer partition."""

    type: Literal["range"] = "range"
    partition_id: str
    column: str
    lower: int
    upper: int


TaskSelection = Annotated[
    AllSelection | ValueSelection | RangeSelection,
    Field(discriminator="type"),
]


class PhysicalPartition(BaseModel):
    """Normalized state for one physical BigQuery integer partition."""

    partition_id: str
    column: str
    lower: int
    upper: int
    signature: str

    def to_selection(self) -> RangeSelection:
        """Return the extraction selection for this partition."""
        return RangeSelection(
            partition_id=self.partition_id,
            column=self.column,
            lower=self.lower,
            upper=self.upper,
        )


class Table(BaseModel):
    bq_table: str
    rls: RlsConfig | None = None
    pg_schema: str | None = None
    indexes: list[IndexConfig] = []

    @property
    def table_name(self) -> str:
        """Return the unqualified source table name."""
        return self.bq_table.split(".")[-1]

    @property
    def resolved_schema(self) -> str:
        """Return the configured PostgreSQL schema or source dataset."""
        if self.pg_schema:
            return self.pg_schema
        return self.bq_table.split(".")[-2]

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
            bq_table=self.bq_table,
            gcs_path=(
                f"s3://{gcs_bucket}/{self.resolved_schema}/"
                f"{self.table_name}{suffix}/data.parquet"
            ),
            selection=selection,
            json_columns=json_columns or [],
        )


class AllTable(Table):
    strategy: Literal[Strategy.ALL] = Strategy.ALL


class AllWithPartitionsTable(Table):
    strategy: Literal[Strategy.ALL_WITH_PARTITIONS] = Strategy.ALL_WITH_PARTITIONS


class WindowTable(Table):
    strategy: Literal[Strategy.WINDOW] = Strategy.WINDOW
    partition: PartitionConfig


class SyncConfig(BaseModel):
    tables: list[TableConfig]


class SyncTask(BaseModel):
    sync_id: str
    bq_table: str
    gcs_path: str
    selection: TaskSelection
    json_columns: list[str] = []


class PartitionedTablePlan(BaseModel):
    """Current and affected physical partitions for one table."""

    table_signature: str
    full_rebuild: bool
    """Whether every current partition must be reloaded.

    Set when the table-level signature changes (schema, RLS, indexes, or
    BigQuery schema drift) or on first sync. A signature change is treated
    as an all-partitions rebuild rather than diffed field-by-field, trading
    a rare full reload for not needing separate schema-change detection.
    """
    current_partitions: dict[str, PhysicalPartition]
    changed_paths: dict[str, str]
    removed_partitions: dict[str, PhysicalPartition]

    @model_validator(mode="after")
    def validate_partition_sets(self) -> Self:
        """Require changed and removed IDs to match their respective manifests."""
        if not set(self.changed_paths) <= set(self.current_partitions):
            msg = "Changed partition paths must exist in the current manifest"
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


class FinalizeMessage(BaseModel):
    sync_id: str


class ShutdownMessage(BaseModel):
    sync_id: str


TableConfig = Annotated[
    AllTable | AllWithPartitionsTable | WindowTable,
    Field(discriminator="strategy"),
]
