"""Data models for the sync pipeline."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Strategy(StrEnum):
    DUMP = "dump"
    WINDOW = "window"


class PartitionConfig(BaseModel):
    column: str
    n: int


class RlsConfig(BaseModel):
    """Row-level security configuration for a synced table."""

    column: str


class Table(BaseModel):
    bq_table: str
    rls: RlsConfig | None = None
    pg_schema: str | None = None

    @property
    def table_name(self) -> str:
        return self.bq_table.split(".")[-1]

    @property
    def resolved_schema(self) -> str:
        if self.pg_schema:
            return self.pg_schema
        return self.bq_table.split(".")[-2]

    def to_task(
        self,
        sync_id: str,
        gcs_bucket: str,
        partition_value: str | None = None,
        partition_column: str | None = None,
    ) -> SyncTask:
        suffix = f"/{partition_value}" if partition_value else ""
        return SyncTask(
            sync_id=sync_id,
            bq_table=self.bq_table,
            gcs_path=f"s3://{gcs_bucket}/{self.table_name}{suffix}/data.parquet",
            partition_column=partition_column,
            partition_value=partition_value,
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


class FinalizeMessage(BaseModel):
    sync_id: str


TableConfig = Annotated[DumpTable | WindowTable, Field(discriminator="strategy")]
