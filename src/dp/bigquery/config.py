"""BigQuery partition configuration and signature helpers."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from typing import cast

from google.cloud.bigquery import RangePartitioning, Table, TimePartitioning
from whenever import PlainDateTime


@dataclass(slots=True)
class RangeConfig:
    """Validated integer-range partition configuration."""

    field: str
    start: int
    end: int
    interval: int

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("Range partition field must not be empty")
        if self.interval <= 0:
            raise ValueError("Range partition interval must be positive")
        if self.start >= self.end:
            raise ValueError("Range partition start must precede end")


@dataclass(frozen=True, slots=True)
class TimePartitionSpec:
    """How to parse and step through a time partition granularity."""

    strptime_format: str
    step: Callable[[PlainDateTime], PlainDateTime]
    output_pattern: str


class TimeGranularity(StrEnum):
    """Supported BigQuery time partition granularities."""

    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"


@dataclass(slots=True)
class TimeConfig:
    """Validated time-partition configuration."""

    field: str
    granularity: TimeGranularity

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("Time partition field must not be empty")


PartitionKindConfig = RangeConfig | TimeConfig


def range_config(partitioning: RangePartitioning, table: str) -> RangeConfig:
    """Return validated integer-range configuration from metadata."""
    match (
        partitioning.field,
        partitioning.range_.start,
        partitioning.range_.end,
        partitioning.range_.interval,
    ):
        case str(field), (None | 0), int(end), int(interval):
            start = 0
        case str(field), int(start), int(end), int(interval):
            pass
        case _:
            raise ValueError(f"Incomplete range partition metadata: {table}")
    try:
        return RangeConfig(field, start, end, interval)
    except ValueError as error:
        raise ValueError(f"Invalid range partition metadata: {table}") from error


def time_config(partitioning: TimePartitioning, table: str) -> TimeConfig:
    """Return validated time-partition configuration from metadata."""
    field = partitioning.field
    if field is None:
        raise ValueError(
            f"Ingestion-time partitioning without an explicit field is unsupported: {table}"
        )
    assert isinstance(field, str)
    raw = partitioning.type_ or TimeGranularity.DAY
    try:
        return TimeConfig(field, TimeGranularity(raw))
    except ValueError as error:
        raise ValueError(
            f"Unsupported time partition granularity {raw}: {table}"
        ) from error


def partition_kind_config(metadata: Table, table: str) -> PartitionKindConfig:
    """Return time or range partition configuration for a physical table."""
    if metadata.table_type != "TABLE":
        raise ValueError(
            f"partitioned requires a physically partitioned table: {table}"
        )
    if metadata.range_partitioning is not None:
        return range_config(metadata.range_partitioning, table)
    if metadata.time_partitioning is not None:
        return time_config(metadata.time_partitioning, table)
    raise ValueError(
        f"partitioned requires a time- or range-partitioned table: {table}"
    )


def partitioned_table_signature(
    metadata: Table, config_json: str, kind_config: PartitionKindConfig
) -> str:
    """Hash source schema, partition metadata, and synchronization configuration."""
    return sha256(
        json.dumps(
            {
                "config": config_json,
                **asdict(kind_config),
                "schema": repr(cast(object, metadata.schema)),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
