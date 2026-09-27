"""Unit tests for BigQuery partition metadata and normalization."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from google.cloud.bigquery import Row
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from data_proxy.bigquery.clients import BigQuery
from data_proxy.bigquery.config import (
    PartitionKindConfig,
    RangeConfig,
    TimeConfig,
    TimeGranularity,
)
from data_proxy.bigquery.partitions import (
    PartitionNormalizer,
    parse_table_reference,
    partition_rows,
    physical_partitions,
)
from data_proxy.models import (
    PartitionMetadata,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from tests.helpers import metadata_row


class TestPartitionMetadata:
    """PartitionMetadata behavior tests."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("values", "field"),
        [
            ((123, datetime(2025, 1, 1, tzinfo=UTC), 128), "partition_id"),
            (("1", "not a timestamp", 128), "last_modified_time"),
            (("1", datetime(2025, 1, 1, tzinfo=UTC), "128"), "logical_bytes"),
        ],
        ids=[
            "partition-id-is-not-text",
            "modified-time-is-not-datetime",
            "bytes-is-not-int",
        ],
    )
    async def test_rejects_invalid_partition_query_rows(
        self, values: tuple[str | int | datetime, ...], field: str
    ) -> None:
        """Reject driver rows with values that do not match the query schema."""
        conn = AsyncMock(spec=BigQuery)
        conn.query.return_value = [
            Row(
                values,
                {
                    "partition_id": 0,
                    "last_modified_time": 1,
                    "logical_bytes": 2,
                },
            )
        ]

        with pytest.raises(ValidationError, match=field):
            await partition_rows(conn, "project", "dataset", "table")

    def test_defaults_missing_logical_bytes_to_zero(self) -> None:
        """Default missing logical bytes to zero during normalization."""
        row = PartitionMetadata(
            partition_id="0",
            last_modified_time=datetime(2025, 1, 1, tzinfo=UTC),
            logical_bytes=None,
        )
        partition = PartitionNormalizer(
            RangeConfig(field="id", start=0, end=10, interval=10),
            "p.d.t",
            "signature",
        ).normalize(row)

        assert partition is not None
        assert partition.logical_bytes == 0

    @pytest.mark.asyncio
    async def test_partition_rows_validate_bigquery_rows(self) -> None:
        """Convert a BigQuery row to its validated metadata model."""
        conn = AsyncMock(spec=BigQuery)
        conn.query.return_value = [
            Row(
                ("20250101", datetime(2025, 1, 1, tzinfo=UTC), 128),
                {
                    "partition_id": 0,
                    "last_modified_time": 1,
                    "logical_bytes": 2,
                },
            )
        ]

        rows = await partition_rows(conn, "project", "dataset", "table")

        assert rows == [
            PartitionMetadata(
                partition_id="20250101",
                last_modified_time=datetime(2025, 1, 1, tzinfo=UTC),
                logical_bytes=128,
            )
        ]

    def test_rejects_unknown_partition_kind(
        self, invalid_kind_config: PartitionKindConfig
    ) -> None:
        """Reject an unknown partition kind."""
        normalizer = PartitionNormalizer(
            kind_config=invalid_kind_config, table="p.d.t", signature="sig"
        )
        row = PartitionMetadata(
            partition_id="0",
            last_modified_time=datetime(2025, 1, 1, tzinfo=UTC),
            logical_bytes=128,
        )
        with pytest.raises(AssertionError):
            normalizer.normalize(row)


class TestPhysicalPartitions:
    """Physical partition discovery behavior tests."""

    @pytest.mark.asyncio
    async def test_limits_time_partitions_to_latest_n(self, bigquery: BigQuery) -> None:
        """Keep only the latest time partitions when n is configured."""
        signature, partitions = await physical_partitions(
            bigquery,
            "test.dataset.time_day",
            "{}",
            n=2,
        )

        assert signature
        assert set(partitions) == {"20250102", "20250103"}

    @pytest.mark.asyncio
    async def test_rejects_n_for_range_partitions(self, bigquery: BigQuery) -> None:
        """Reject a retention count for range-partitioned tables."""
        with pytest.raises(ValueError, match="only supported"):
            await physical_partitions(
                bigquery,
                "test.dataset.range_buckets",
                "{}",
                n=1,
            )


class TestTableReference:
    """TableReference behavior tests."""

    def test_parses_project_dataset_and_table(self) -> None:
        """Parse a table reference into named parts."""
        reference = parse_table_reference("project.dataset.table-name")
        assert reference.project == "project"
        assert reference.dataset == "dataset"
        assert reference.table == "table-name"

    @given(
        reference=st.text(min_size=1, max_size=30).filter(
            lambda value: value.count(".") != 2
        )
    )
    def test_rejects_table_reference_with_wrong_component_count(
        self, reference: str
    ) -> None:
        """Reject a table reference without three components."""
        with pytest.raises(ValueError, match="unpack"):
            parse_table_reference(reference)


class TestPartitionConfiguration:
    """PartitionConfiguration behavior tests."""

    @given(
        case=st.sampled_from(
            [
                ("", 0, 10, 1, "field must not be empty"),
                ("id", 0, 10, 0, "interval must be positive"),
                ("id", 10, 10, 1, "start must precede end"),
            ]
        )
    )
    def test_rejects_invalid_range_configuration(
        self, case: tuple[str, int, int, int, str]
    ) -> None:
        """Reject invalid range partition configuration."""
        with pytest.raises(ValueError, match=case[4]):
            RangeConfig(field=case[0], start=case[1], end=case[2], interval=case[3])

    def test_rejects_empty_time_partition_field(self) -> None:
        """Reject an empty time partition field."""
        with pytest.raises(ValueError, match="field must not be empty"):
            TimeConfig(field="", granularity=TimeGranularity.DAY)


class TestRangePartitionNormalization:
    """Range partition normalization behavior tests."""

    @given(bucket=st.integers(0, 9), logical_bytes=st.integers(0, 1000000))
    def test_normalizes_aligned_range_bucket(
        self, bucket: int, logical_bytes: int
    ) -> None:
        """Normalize an aligned range bucket into integer bounds."""
        config = RangeConfig(field="id", start=0, end=100, interval=10)
        partition = PartitionNormalizer(config, "p.d.t", "signature").normalize(
            metadata_row(str(bucket * 10), logical_bytes)
        )
        assert partition is not None
        assert isinstance(partition.selection, RangeSelection)
        assert partition.selection.lower == bucket * 10
        assert partition.selection.upper == min(bucket * 10 + 10, 100)
        assert partition.logical_bytes == logical_bytes

    @given(
        partition_id=st.one_of(
            st.integers(-100, -1),
            st.integers(100, 200),
            st.integers(0, 99).filter(lambda value: value % 10 != 0),
        )
    )
    def test_rejects_invalid_range_bucket(self, partition_id: int) -> None:
        """Reject an out-of-range or unaligned bucket."""
        with pytest.raises(ValueError, match="Invalid range partition ID"):
            PartitionNormalizer(
                RangeConfig(field="id", start=0, end=100, interval=10),
                "p.d.t",
                "signature",
            ).normalize(metadata_row(str(partition_id), 0))

    def test_rejects_upper_bound_range_bucket(self) -> None:
        """Reject a range bucket at the exclusive upper bound."""
        with pytest.raises(ValueError, match="Invalid range partition ID"):
            PartitionNormalizer(
                RangeConfig(field="id", start=0, end=100, interval=10),
                "p.d.t",
                "signature",
            ).normalize(metadata_row("100", 0))

    def test_accepts_alignment_with_nonzero_start(self) -> None:
        """Accept an interval-aligned bucket after a nonzero start."""
        partition = PartitionNormalizer(
            RangeConfig(field="id", start=3, end=103, interval=10),
            "p.d.t",
            "signature",
        ).normalize(metadata_row("13", 0))
        assert partition is not None
        assert isinstance(partition.selection, RangeSelection)
        assert partition.selection.lower == 13
        assert partition.selection.upper == 23

    def test_rejects_unaligned_range_bucket(self) -> None:
        """Reject a range bucket that is not interval-aligned."""
        with pytest.raises(ValueError, match="Invalid range partition ID"):
            PartitionNormalizer(
                RangeConfig(field="id", start=0, end=100, interval=10),
                "p.d.t",
                "signature",
            ).normalize(metadata_row("1", 0))

    def test_rejects_aligned_upper_boundary_with_nonzero_start(self) -> None:
        """Reject an aligned bucket at a nonzero exclusive upper bound."""
        with pytest.raises(ValueError, match="Invalid range partition ID"):
            PartitionNormalizer(
                RangeConfig(field="id", start=3, end=103, interval=10),
                "p.d.t",
                "signature",
            ).normalize(metadata_row("103", 0))

    def test_normalizes_null_range_bucket_as_remainder(self) -> None:
        """Normalize a null range bucket as a remainder selection."""
        partition = PartitionNormalizer(
            RangeConfig(field="id", start=0, end=100, interval=10), "p.d.t", "signature"
        ).normalize(metadata_row("__NULL__", 0))
        assert partition is not None
        assert isinstance(partition.selection, RemainderSelection)
        assert partition.selection.start == 0
        assert partition.selection.end == 100


class TestTimePartitionNormalization:
    """Time partition normalization behavior tests."""

    @given(granularity=st.sampled_from(list(TimeGranularity)))
    def test_advances_time_partition_bounds(self, granularity: TimeGranularity) -> None:
        """Advance time bounds according to the selected granularity."""
        config = TimeConfig(field="created_at", granularity=granularity)
        normalizer = PartitionNormalizer(config, "p.d.t", "signature")
        partition_id = {
            TimeGranularity.HOUR: "2025010112",
            TimeGranularity.DAY: "20250101",
            TimeGranularity.MONTH: "202501",
            TimeGranularity.YEAR: "2025",
        }[granularity]
        lower, upper = normalizer.time_bounds(partition_id, granularity)
        assert lower < upper

    @given(granularity=st.sampled_from(list(TimeGranularity)))
    def test_normalizes_non_null_time_partition(
        self, granularity: TimeGranularity
    ) -> None:
        """Normalize a non-null time partition into a time range."""
        partition_id = {
            TimeGranularity.HOUR: "2025010112",
            TimeGranularity.DAY: "20250101",
            TimeGranularity.MONTH: "202501",
            TimeGranularity.YEAR: "2025",
        }[granularity]
        partition = PartitionNormalizer(
            TimeConfig(field="created_at", granularity=granularity),
            "p.d.t",
            "signature",
        ).normalize(metadata_row(partition_id, 0))
        assert partition is not None
        assert isinstance(partition.selection, TimeRangeSelection)
        assert partition.selection.lower < partition.selection.upper

    @given(day=st.integers(1, 28), hour=st.integers(0, 23))
    def test_changes_signature_when_metadata_changes(self, day: int, hour: int) -> None:
        """Change the partition signature when modification time changes."""
        first = PartitionNormalizer(
            RangeConfig(field="id", start=0, end=100, interval=10), "p.d.t", "signature"
        ).normalize(metadata_row("0", 0, datetime(2025, 1, day, hour, tzinfo=UTC)))
        second = PartitionNormalizer(
            RangeConfig(field="id", start=0, end=100, interval=10), "p.d.t", "signature"
        ).normalize(metadata_row("0", 0, datetime(2025, 2, day, hour, tzinfo=UTC)))
        assert first is not None
        assert second is not None
        assert first.signature != second.signature

    def test_rejects_missing_modification_time(self) -> None:
        """Reject metadata without a modification timestamp."""
        with pytest.raises(TypeError, match="Missing partition modification time"):
            PartitionNormalizer(
                RangeConfig(field="id", start=0, end=100, interval=10),
                "p.d.t",
                "signature",
            ).normalize(metadata_row("0", 0, missing_modified=True))

    def test_skips_null_time_bucket(self) -> None:
        """Skip a null bucket for time partitioning."""
        partition = PartitionNormalizer(
            TimeConfig(field="created_at", granularity=TimeGranularity.DAY),
            "p.d.t",
            "signature",
        ).normalize(metadata_row("__NULL__", 0))
        assert partition is None
