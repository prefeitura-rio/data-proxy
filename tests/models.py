"""Models used by the test fixtures."""

from datetime import datetime

from google.cloud.bigquery import (
    PartitionRange,
    RangePartitioning,
    Table,
    TimePartitioning,
)
from pydantic import BaseModel


class BigQueryMetadataRow(BaseModel):
    """Validated metadata row loaded from the BigQuery CSV."""

    table_name: str
    table_type: str
    partition_kind: str
    partition_field: str | None
    range_start: int | None
    range_end: int | None
    range_interval: int | None
    time_granularity: str | None
    modified: datetime

    @property
    def last_modified_millis(self) -> str:
        """Return the BigQuery API timestamp representation."""
        return str(int(self.modified.timestamp() * 1000))

    @property
    def range_partitioning(self) -> RangePartitioning | None:
        """Return validated range metadata when this is a range table."""
        if self.partition_kind != "range":
            return None
        if (
            self.partition_field is None
            or self.range_start is None
            or self.range_end is None
            or self.range_interval is None
        ):
            raise ValueError("Range metadata is incomplete")
        return RangePartitioning(
            field=self.partition_field,
            range_=PartitionRange(
                start=self.range_start,
                end=self.range_end,
                interval=self.range_interval,
            ),
        )

    def to_table(self) -> Table:
        """Return the BigQuery table represented by this metadata row."""
        table = Table(self.table_name)
        table._properties["type"] = self.table_type
        table._properties["lastModifiedTime"] = self.last_modified_millis
        table.range_partitioning = self.range_partitioning
        table.time_partitioning = self.time_partitioning
        return table

    @property
    def time_partitioning(self) -> TimePartitioning | None:
        """Return validated time metadata when this is a time table."""
        if self.partition_kind != "time":
            return None
        if self.partition_field is None or self.time_granularity is None:
            raise ValueError("Time metadata is incomplete")
        return TimePartitioning(
            field=self.partition_field,
            type_=self.time_granularity,
        )


class BigQueryPartitionRow(BaseModel):
    """Validated partition row loaded from the BigQuery CSV."""

    partition_id: str
    last_modified_time: datetime
