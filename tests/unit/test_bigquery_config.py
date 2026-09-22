"""Unit tests for BigQuery partition configuration."""

from collections.abc import Callable
from typing import cast

import pytest
from google.cloud.bigquery import (
    PartitionRange,
    RangePartitioning,
    Table,
    TimePartitioning,
)
from hypothesis import given
from hypothesis import strategies as st
from whenever import PlainDateTime

from data_proxy.bigquery.config import (
    RangeConfig,
    TimeConfig,
    TimeGranularity,
    partition_kind_config,
    range_config,
    time_config,
)
from tests.strategies import identifiers, ordered_bounds

table_from_api = cast(Callable[[dict[str, object]], Table], Table.from_api_repr)


class TestTimeGranularity:
    """Time granularity behavior tests."""

    @given(granularity=st.sampled_from(list(TimeGranularity)))
    def test_steps_exact_time_boundary(self, granularity: TimeGranularity) -> None:
        """Step each time granularity to its exact next boundary."""
        start = PlainDateTime(2025, 1, 1, 12)
        spec = granularity.spec()
        end = spec.step(start)
        expected = {
            TimeGranularity.HOUR: ("2025-01-01 12:00:00", "2025-01-01 13:00:00"),
            TimeGranularity.DAY: ("2025-01-01", "2025-01-02"),
            TimeGranularity.MONTH: ("2025-01-01", "2025-02-01"),
            TimeGranularity.YEAR: ("2025-01-01", "2026-01-01"),
        }[granularity]
        assert start.format(spec.output_pattern) == expected[0]
        assert end.format(spec.output_pattern) == expected[1]


class TestRangeConfiguration:
    """Range partition configuration behavior tests."""

    @given(field=identifiers, bounds=ordered_bounds(), interval=st.integers(1, 100))
    def test_converts_valid_range_metadata(
        self, field: str, bounds: tuple[int, int], interval: int
    ) -> None:
        """Convert valid range metadata into application configuration."""
        start, end = bounds
        metadata = RangePartitioning(
            field=field,
            range_=PartitionRange(start=start, end=end, interval=interval),
        )
        config = range_config(metadata, "p.d.t")
        assert config == RangeConfig(
            field=field, start=start, end=end, interval=interval
        )

    @given(field=identifiers, start=st.integers(-10, 10))
    def test_rejects_incomplete_range_metadata(self, field: str, start: int) -> None:
        """Reject range metadata without an end or interval."""
        metadata = RangePartitioning(
            field=field, range_=PartitionRange(start=start, end=None, interval=None)
        )
        with pytest.raises(ValueError, match="Incomplete"):
            range_config(metadata, "p.d.t")

    @given(field=identifiers, bounds=ordered_bounds())
    def test_rejects_invalid_range_metadata(
        self, field: str, bounds: tuple[int, int]
    ) -> None:
        """Reject range metadata with an invalid interval or bounds."""
        start, end = bounds
        metadata = RangePartitioning(
            field=field,
            range_=PartitionRange(start=start, end=end, interval=0),
        )
        with pytest.raises(ValueError, match="Invalid"):
            range_config(metadata, "p.d.t")


class TestTimeConfiguration:
    """Time partition configuration behavior tests."""

    @given(field=identifiers, granularity=st.sampled_from(list(TimeGranularity)))
    def test_converts_valid_time_metadata(
        self, field: str, granularity: TimeGranularity
    ) -> None:
        """Convert valid time metadata into application configuration."""
        metadata = TimePartitioning(type_=granularity.value, field=field)
        assert time_config(metadata, "p.d.t") == TimeConfig(
            field=field, granularity=granularity
        )

    def test_defaults_missing_time_granularity_to_day(self) -> None:
        """Default missing time granularity to DAY."""
        assert time_config(
            TimePartitioning(type_=None, field="created_at"), "p.d.t"
        ) == TimeConfig(field="created_at", granularity=TimeGranularity.DAY)

    def test_rejects_ingestion_time_metadata(self) -> None:
        """Reject time metadata without an explicit field."""
        with pytest.raises(ValueError, match="Ingestion-time"):
            time_config(TimePartitioning(field=None), "p.d.t")

    @given(
        field=identifiers, granularity=st.sampled_from(["WEEK", "MINUTE", "UNKNOWN"])
    )
    def test_rejects_unsupported_time_granularity(
        self, field: str, granularity: str
    ) -> None:
        """Reject unsupported time partition granularities."""
        with pytest.raises(ValueError, match="Unsupported"):
            time_config(TimePartitioning(type_=granularity, field=field), "p.d.t")


class TestPartitionKindConfiguration:
    """Partition kind selection behavior tests."""

    def test_selects_range_partition_configuration(self) -> None:
        """Select the range configuration for a range-partitioned table."""
        metadata = table_from_api(
            {
                "tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                "type": "TABLE",
                "rangePartitioning": {
                    "field": "id",
                    "range": {"start": 0, "end": 100, "interval": 10},
                },
            }
        )
        assert partition_kind_config(metadata, "p.d.t").kind == "range"

    def test_selects_time_partition_configuration(self) -> None:
        """Select the time configuration for a time-partitioned table."""
        metadata = table_from_api(
            {
                "tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                "type": "TABLE",
                "timePartitioning": {"type": "DAY", "field": "created_at"},
            }
        )
        assert partition_kind_config(metadata, "p.d.t").kind == "time"

    def test_rejects_unpartitioned_table(self) -> None:
        """Reject an unpartitioned table for partitioned synchronization."""
        metadata = table_from_api(
            {
                "tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                "type": "TABLE",
            }
        )
        with pytest.raises(ValueError, match="time- or range-partitioned"):
            partition_kind_config(metadata, "p.d.t")

    def test_rejects_nonphysical_table(self) -> None:
        """Reject a nonphysical BigQuery table."""
        metadata = table_from_api(
            {
                "tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                "type": "VIEW",
            }
        )
        with pytest.raises(ValueError, match="physically partitioned"):
            partition_kind_config(metadata, "p.d.t")
