"""Neutral physical partition configuration types for ingestion sources."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from functools import partial
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, model_validator
from whenever import PlainDateTime

from ..constants import TIME_GRANULARITY_SPECS

NonEmptyString = Annotated[str, Field(min_length=1)]


class AllSelection(BaseModel):
    """Select every row from a source table."""

    type: Literal["all"] = "all"

    def check_id(self, partition_id: str) -> None:
        """Accept every partition ID for a whole-table selection."""


class TimeRangeSelection(BaseModel):
    """Select rows in one time partition's [lower, upper) bounds."""

    type: Literal["time_range"] = "time_range"
    column: NonEmptyString
    lower: NonEmptyString
    upper: NonEmptyString

    @model_validator(mode="after")
    def validate_bounds(self) -> TimeRangeSelection:
        """Require chronological lower and upper bounds."""
        if self.lower >= self.upper:
            raise ValueError("Time selection lower bound must precede upper bound")
        return self

    def check_id(self, partition_id: str) -> None:
        """Accept every partition ID for a time-range selection."""


class RangeSelection(BaseModel):
    """Select rows within one physical integer partition."""

    type: Literal["range"] = "range"
    partition_id: NonEmptyString
    column: NonEmptyString
    lower: int
    upper: int

    @model_validator(mode="after")
    def validate_bounds(self) -> RangeSelection:
        """Require a non-empty integer range."""
        if self.lower >= self.upper:
            raise ValueError("Range selection lower bound must precede upper bound")
        return self

    def check_id(self, partition_id: str) -> None:
        """Require the selection ID to match the physical partition."""
        if self.partition_id != partition_id:
            raise ValueError(
                "Range selection partition ID must match physical partition"
            )


class RemainderSelection(BaseModel):
    """Select rows outside an integer range, including nulls."""

    type: Literal["remainder"] = "remainder"
    column: NonEmptyString
    start: int
    end: int

    @model_validator(mode="after")
    def validate_bounds(self) -> RemainderSelection:
        """Require a non-empty remainder range."""
        if self.start >= self.end:
            raise ValueError("Remainder selection start must precede end")
        return self

    def check_id(self, partition_id: str) -> None:
        """Accept every partition ID for a remainder selection."""


TaskSelection = Annotated[
    AllSelection | TimeRangeSelection | RangeSelection | RemainderSelection,
    Field(discriminator="type"),
]


@dataclass(frozen=True, slots=True)
class PartitionRequest:
    """One source request to discover physical partitions."""

    table: str
    config: dict[str, JsonValue]
    keep_latest: int | None


class PhysicalPartition(BaseModel):
    """Normalized state and extraction selection for one physical partition."""

    partition_id: NonEmptyString
    signature: NonEmptyString
    selection: TimeRangeSelection | RangeSelection | RemainderSelection
    logical_bytes: int = 0
    """Uncompressed source size, used to group extraction batches."""

    @model_validator(mode="after")
    def validate_range_partition_id(self) -> PhysicalPartition:
        """Require range selection IDs to match their physical partition."""
        self.selection.check_id(self.partition_id)
        return self


class TimeGranularity(StrEnum):
    """Supported source time partition granularities."""

    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"

    def spec(self) -> TimePartitionSpec:
        """Return the parse and step specification for this granularity."""
        fmt, unit, pattern = TIME_GRANULARITY_SPECS[self.value]
        return TimePartitionSpec(
            fmt,
            partial(PlainDateTime.add, **{unit: 1}, naive_arithmetic_ok=True),
            pattern,
        )


@dataclass(frozen=True, slots=True)
class TimePartitionSpec:
    """How to parse and step through a time partition granularity."""

    strptime_format: str
    step: Callable[[PlainDateTime], PlainDateTime]
    output_pattern: str


@dataclass(slots=True)
class RangeConfig:
    """Validated integer-range partition configuration."""

    kind: Literal["range"] = "range"
    field: str = ""
    start: int = 0
    end: int = 0
    interval: int = 0

    def __post_init__(self) -> None:
        """Require a non-empty, increasing range with a positive interval."""
        if not self.field:
            raise ValueError("Range partition field must not be empty")
        if self.interval <= 0:
            raise ValueError("Range partition interval must be positive")
        if self.start >= self.end:
            raise ValueError("Range partition start must precede end")


@dataclass(slots=True)
class TimeConfig:
    """Validated time-partition configuration."""

    kind: Literal["time"] = "time"
    field: str = ""
    granularity: TimeGranularity = TimeGranularity.DAY

    def __post_init__(self) -> None:
        """Require an explicit partition field."""
        if not self.field:
            raise ValueError("Time partition field must not be empty")


type PartitionKindConfig = RangeConfig | TimeConfig


def partitioned_table_signature(
    config_json: str, kind_config: PartitionKindConfig, schema: str
) -> str:
    """Hash source schema, partition metadata, and sync configuration."""
    return sha256(
        json.dumps(
            {"config": config_json, **asdict(kind_config), "schema": schema},
            sort_keys=True,
        ).encode()
    ).hexdigest()
