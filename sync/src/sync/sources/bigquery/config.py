"""BigQuery partition metadata configuration helpers."""

from typing import cast

from google.cloud.bigquery import (
    RangePartitioning,
    SchemaField,
    Table,
    TimePartitioning,
)

from ..partitions import (
    PartitionKindConfig,
    RangeConfig,
    TimeConfig,
    TimeGranularity,
)
from ..partitions import (
    partitioned_table_signature as source_partitioned_table_signature,
)


def range_config(partitioning: RangePartitioning, table: str) -> RangeConfig:
    """Return validated integer-range configuration from metadata."""
    field = partitioning.field
    start = partitioning.range_.start or 0
    end = partitioning.range_.end
    interval = partitioning.range_.interval

    match field:
        case str() if field and end is not None and interval is not None:
            pass
        case _:
            raise ValueError(f"Incomplete range partition metadata: {table}")

    try:
        return RangeConfig(field=field, start=start, end=end, interval=interval)
    except ValueError as error:
        raise ValueError(f"Invalid range partition metadata: {table}") from error


def time_config(partitioning: TimePartitioning, table: str) -> TimeConfig:
    """Return validated time-partition configuration from metadata."""
    field = partitioning.field
    if field is None:
        raise ValueError(
            f"Ingestion-time partitioning without an explicit field is unsupported: {table}"
        )

    if not isinstance(field, str) or not field:
        raise ValueError(f"Invalid time partition field: {table}")

    raw = partitioning.type_ or TimeGranularity.DAY

    try:
        return TimeConfig(field=field, granularity=TimeGranularity(raw))
    except ValueError as error:
        raise ValueError(
            f"Unsupported time partition granularity {raw}: {table}"
        ) from error


def partition_kind_config(metadata: Table, table: str) -> PartitionKindConfig:
    """Return time or range partition configuration for a physical table."""
    match metadata:
        case Table(table_type="TABLE", range_partitioning=RangePartitioning() as rp):
            return range_config(rp, table)
        case Table(table_type="TABLE", time_partitioning=TimePartitioning() as tp):
            return time_config(tp, table)
        case Table(table_type="TABLE"):
            raise ValueError(
                "partitioned requires a time- or range-partitioned table: " + table
            )
        case _:
            raise ValueError(
                "partitioned requires a physically partitioned table: " + table
            )


def partitioned_table_signature(
    metadata: Table, config_json: str, kind_config: PartitionKindConfig
) -> str:
    """Hash BigQuery schema, partition metadata, and sync configuration."""
    return source_partitioned_table_signature(
        config_json,
        kind_config,
        repr(cast(list[SchemaField], metadata.schema)),
    )
