"""Data models for the sync service."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self, override

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PositiveInt,
    computed_field,
    model_validator,
)

from .constants import DEFAULT_SOURCE
from .sources.partitions import PhysicalPartition, TaskSelection
from .sources.registry import sources
from .types import NonEmptyString
from .utils import sha256_hex


class Strategy(StrEnum):
    """Table synchronization strategy: whole-table or physically partitioned."""

    FULL = "full"
    PARTITIONED = "partitioned"


class UnitMapping(BaseModel):
    """One row column that identifies membership in a unit of the given type."""

    column: NonEmptyString
    unit_type: NonEmptyString


class DuckLakePartitionTransform(StrEnum):
    """DuckLake partition transforms supported by sync configuration."""

    IDENTITY = "identity"
    BUCKET = "bucket"
    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"


class DuckLakePartition(BaseModel):
    """One custom DuckLake partition transform."""

    column: NonEmptyString
    transform: DuckLakePartitionTransform = DuckLakePartitionTransform.IDENTITY
    buckets: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_buckets(self) -> Self:
        """Require buckets only for the bucket transform."""
        if self.transform == DuckLakePartitionTransform.BUCKET:
            if self.buckets is None:
                raise ValueError("Bucket partition transforms require buckets")
        elif self.buckets is not None:
            raise ValueError("Only bucket partition transforms accept buckets")
        return self


class DuckLakeSchemaConfig(BaseModel):
    """Schema-level DuckLake settings."""

    encrypted: bool = False


class DuckLakeTableConfig(BaseModel):
    """Table-level DuckLake settings."""

    partitioning: list[DuckLakePartition] | None = None
    sort: list[NonEmptyString] | None = None
    encrypted: bool | None = None

    @model_validator(mode="after")
    def validate_sort(self) -> Self:
        """Reject an explicitly empty sort configuration."""
        if self.sort == []:
            raise ValueError("DuckLake sort must contain at least one column")
        return self

    @model_validator(mode="after")
    def reject_encryption(self) -> Self:
        """Require encryption to be configured at schema scope."""
        if self.encrypted is not None:
            raise ValueError("DuckLake encryption must be configured at schema scope")
        return self


class Table(BaseModel):
    """Common configuration shared by every synced table strategy."""

    model_config: ClassVar[ConfigDict] = ConfigDict({"extra": "forbid"})

    name: NonEmptyString
    rls: list[UnitMapping] | None = None
    cache_ttl: int | None = None
    """Lifetime of a proxy cache entry for this table, in seconds."""
    ducklake: DuckLakeTableConfig = Field(default_factory=DuckLakeTableConfig)
    resolved_schema: str = ""
    """The schema this table is nested under. Stamped by SyncConfig, never user input."""
    resolved_source: str = ""
    """The schema source type. Stamped by SyncConfig, never user input."""
    resolved_source_settings: dict[str, JsonValue] | None = None
    """The schema source settings. Stamped by SyncConfig, never user input."""

    @property
    def table_name(self) -> str:
        """Return the unqualified source table name."""
        return self.name.split(".")[-1]

    def config_signature_fields(self) -> dict[str, JsonValue]:
        """Return the configuration fields that identify this table for a sync."""
        source: dict[str, JsonValue] = {"type": self.resolved_source}
        if self.resolved_source_settings is not None:
            source["settings"] = self.resolved_source_settings
        return {
            "name": self.name,
            "source": source,
            "rls": [r.model_dump() for r in self.rls] if self.rls else None,
            "ducklake": self.ducklake.model_dump(),
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
            source=self.resolved_source,
            source_settings=self.resolved_source_settings,
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
    fallback: bool = False
    """Keep only the last N time partitions. Time-partitioned tables only."""

    @override
    def config_signature_fields(self) -> dict[str, JsonValue]:
        """Include the strategy and the partition window."""
        fields = super().config_signature_fields()
        fields["strategy"] = self.strategy
        fields["n"] = self.n
        fields["fallback"] = self.fallback
        return fields


TableConfig = Annotated[
    FullTable | PartitionedTable,
    Field(discriminator="strategy"),
]


class SourceConfig(BaseModel):
    """Non-secret configuration for one schema ingestion source."""

    model_config: ClassVar[ConfigDict] = ConfigDict({"extra": "forbid"})

    type: NonEmptyString = DEFAULT_SOURCE
    settings: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def reject_empty_settings(self) -> Self:
        """Require empty settings to be omitted from source configuration."""
        if self.settings == {}:
            raise ValueError("Empty source settings must be omitted")
        return self


class SchemaConfig(BaseModel):
    """A PostgreSQL schema, its ingestion source, and its synced tables."""

    source: SourceConfig = Field(default_factory=SourceConfig)
    claim: NonEmptyString | None = None
    ducklake: DuckLakeSchemaConfig = Field(default_factory=DuckLakeSchemaConfig)
    tables: list[TableConfig] = Field(default_factory=list)


class SyncConfig(BaseModel):
    """The full set of schemas and their nested tables a synchronization run manages."""

    schemas: dict[str, SchemaConfig] = Field(default_factory=dict)

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
                table.resolved_source = schema.source.type
                table.resolved_source_settings = schema.source.settings

        return self

    @model_validator(mode="after")
    def validate_schema_sources(self) -> Self:
        """Require each schema to use a registered source for every table."""
        for schema in self.schemas.values():
            source = sources.configure(schema.source.type, schema.source.settings)
            for table in schema.tables:
                source.validate(table.name)
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
    def reject_duplicate_destination_table_names(self) -> Self:
        """Require each schema to map every destination table name once."""
        destinations = [
            f"{table.resolved_schema}.{table.table_name}" for table in self.tables
        ]
        duplicates = sorted(
            {name for name in destinations if destinations.count(name) > 1}
        )
        if duplicates:
            raise ValueError(f"Duplicate destination table names: {duplicates}")
        return self

    @model_validator(mode="after")
    def validate_partition_fallbacks(self) -> Self:
        """Allow partition fallback only when the schema source supports it."""
        for table in self.tables:
            if not isinstance(table, PartitionedTable) or not table.fallback:
                continue
            source = sources.configure(
                table.resolved_source, table.resolved_source_settings
            )
            if source.fallback is None:
                raise ValueError(
                    f"Source {source.name!r} does not support partition fallback"
                )
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
    source: NonEmptyString
    source_settings: dict[str, JsonValue] | None = None
    target_schema: str
    bucket_path: str
    selections: Annotated[list[TaskSelection], Field(min_length=1)]
    json_columns: list[str] = Field(default_factory=list)

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
        return sha256_hex(f"{self.run_id}:{self.bucket_path}")


class DumpStatus(StrEnum):
    """Result status for one extraction task."""

    SUCCESS = "success"
    FAILURE = "error"


class DumpResult(BaseModel):
    """Result of one extraction task."""

    model_config: ClassVar[ConfigDict] = ConfigDict({"extra": "forbid"})

    status: DumpStatus = DumpStatus.SUCCESS
    failed_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_failed_paths_on_failure(self) -> Self:
        """Require at least one failed path when the status is failure."""
        if self.status == DumpStatus.FAILURE and not self.failed_paths:
            raise ValueError("Failed extraction must have failed paths")
        return self


class PartitionChange(BaseModel):
    """One partition change: addition, update, or removal."""

    kind: Literal["add", "update", "remove"]
    partition_id: str
    path: str = ""
    previous: PhysicalPartition | None = None
    current: PhysicalPartition | None = None


class PartitionedTablePlan(BaseModel):
    """Current and affected physical partitions for one table."""

    table_signature: str
    full_rebuild: bool
    current_partitions: dict[str, PhysicalPartition]
    changes: dict[str, PartitionChange]

    @model_validator(mode="after")
    def validate_partition_sets(self) -> Self:
        """Require add/update changes to exist in the current manifest and removes to not."""
        for change in self.changes.values():
            if change.kind in ("add", "update"):
                if change.partition_id not in self.current_partitions:
                    msg = "Add/update partitions must exist in the current manifest"
                    raise ValueError(msg)
            elif (
                change.kind == "remove"
                and change.partition_id in self.current_partitions
            ):
                msg = "Removed partitions can't exist in the current manifest"
                raise ValueError(msg)
        return self


class SyncPlan(BaseModel):
    """Immutable publication inputs for one PostgreSQL schema."""

    schema_name: str
    signatures: dict[str, str] = Field(default_factory=dict)
    paths: dict[str, list[str]] = Field(default_factory=dict)
    partitioned_tables: dict[str, PartitionedTablePlan] = Field(default_factory=dict)

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


class ServingDeployments(BaseModel):
    """Names of the Deployments that serve reads and must restart after a publication."""

    poolers: list[str]
    postgrest: list[str]


class PublicationResult(BaseModel):
    """Exact plan and table set published by the publisher."""

    plan: SyncPlan
    published_tables: set[str]
    snapshot_id: int | None = None
    """DuckLake snapshot that holds the published tables, when any were published."""
