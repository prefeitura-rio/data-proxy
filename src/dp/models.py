"""Data models for the sync pipeline."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator


class Strategy(StrEnum):
    DUMP = "dump"
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
        partition_value: str | None = None,
        partition_column: str | None = None,
        json_columns: list[str] | None = None,
    ) -> SyncTask:
        """Create one extraction task for the table or a window value."""
        suffix = f"/{partition_value}" if partition_value else ""
        return SyncTask(
            sync_id=sync_id,
            bq_table=self.bq_table,
            gcs_path=f"s3://{gcs_bucket}/{self.table_name}{suffix}/data.parquet",
            partition_column=partition_column,
            partition_value=partition_value,
            json_columns=json_columns or [],
        )


class DumpTable(Table):
    strategy: Literal[Strategy.DUMP] = Strategy.DUMP


class WindowTable(Table):
    strategy: Literal[Strategy.WINDOW] = Strategy.WINDOW
    partition: PartitionConfig


class SyncConfig(BaseModel):
    tables: list[TableConfig]


class SyncTask(BaseModel):
    sync_id: str
    bq_table: str
    gcs_path: str
    partition_column: str | None = None
    partition_value: str | None = None
    json_columns: list[str] = []


class SyncPlan(BaseModel):
    """Changed table signatures and exact Parquet paths for one run."""

    sync_id: str
    signatures: dict[str, str]
    paths: dict[str, list[str]]

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        """Require one non-empty path list for every changed table."""
        if set(self.signatures) != set(self.paths) or any(
            not paths for paths in self.paths.values()
        ):
            message = "Sync plan signatures and non-empty paths must match"
            raise ValueError(message)
        return self


class FinalizeMessage(BaseModel):
    sync_id: str


class ShutdownMessage(BaseModel):
    sync_id: str


TableConfig = Annotated[DumpTable | WindowTable, Field(discriminator="strategy")]
