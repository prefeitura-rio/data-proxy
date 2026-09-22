"""BigQuery parttion configuration and signature helpers."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from functools import partial
from hashlib import sha256
from typing import Literal, cast

from google.cloud.bigquery import (
    RangePartitioning,
    SchemaField,
    Table,
    TimePartitioning,
)
from whenever import PlainDateTime

from ..constants import TIME_GRANULARITY_SPECS


class TimeGranularity(StrEnum):
    """Supported BigQuery time partition granularities."""

    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"

    def spec(self) -> TimePartitionSpec:
        """Return the parse and step spec for this granularity."""
        fmt, unit, pattern = TIME_GRANULARITY_SPECS[self.value]
        return TimePartitionSpec(
            fmt,
            partial(PlainDateTime.add, **{unit: 1}, naive_arithmetic_ok=True),
            pattern,
        )


@dataclass(slots=True)
class RangeConfig:
    """Validated integer-range partition configuration."""

    kind: Literal["range"] = "range"
    field: str = ""
    start: int = 0
    end: int = 0
    interval: int = 0

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


@dataclass(slots=True)
class TimeConfig:
    """Validated time-partition configuration."""

    kind: Literal["time"] = "time"
    field: str = ""
    granularity: TimeGranularity = TimeGranularity.DAY

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("Time partition field must not be empty")


type PartitionKindConfig = RangeConfig | TimeConfig


def range_config(partitioning: RangePartitioning, table: str) -> RangeConfig:
    """Return validated integer-range configuration from metadata."""
    field = partitioning.field
    start = partitioning.range_.start or 0
    end = partitioning.range_.end
    interval = partitioning.range_.interval

    if not field or end is None or interval is None:
        raise ValueError(f"Incomplete range partition metadata: {table}")

    try:
        return RangeConfig(
            field=cast("str", field), start=start, end=end, interval=interval
        )
    except ValueError as error:
        raise ValueError(f"Invalid range partition metadata: {table}") from error


def time_config(partitioning: TimePartitioning, table: str) -> TimeConfig:
    """Return validated time-partition configuration from metadata."""
    if partitioning.field is None:
        raise ValueError(
            f"Ingestion-time partitioning without an explicit field is unsupported: {table}"
        )

    raw = partitioning.type_ or TimeGranularity.DAY

    try:
        return TimeConfig(
            field=cast("str", partitioning.field), granularity=TimeGranularity(raw)
        )
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
                f"partitioned requires a time- or range-partitioned table: {table}"
            )
        case _:
            raise ValueError(
                f"partitioned requires a physically partitioned table: {table}"
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
                "schema": repr(cast(list[SchemaField], metadata.schema)),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
