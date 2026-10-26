from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from google.cloud.bigquery import (
    PartitionRange,
    RangePartitioning,
    Row,
    Table,
    TimePartitioning,
)
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from whenever import PlainDateTime

from sync.sources.bigquery.clients import BigQuery
from sync.sources.bigquery.config import (
    partition_kind_config,
    range_config,
    time_config,
)
from sync.sources.bigquery.partitions import (
    PartitionMetadata,
    PartitionNormalizer,
    parse_table_reference,
    partition_rows,
    physical_partitions,
    table_modified,
)
from sync.sources.partitions import (
    RangeConfig,
    RangeSelection,
    RemainderSelection,
    TimeConfig,
    TimeGranularity,
    TimeRangeSelection,
)
from tests.helpers import metadata_row
from tests.strategies import identifiers, ordered_bounds

table_from_api = Table.from_api_repr


class TestTimeGranularity:
    """Time granularity behavior tests."""

    @pytest.mark.parametrize(
        ("granularity", "lower", "upper"),
        [
            pytest.param(
                TimeGranularity.HOUR,
                "2025-01-01 12:00:00",
                "2025-01-01 13:00:00",
                id="hour",
            ),
            pytest.param(TimeGranularity.DAY, "2025-01-01", "2025-01-02", id="day"),
            pytest.param(TimeGranularity.MONTH, "2025-01-01", "2025-02-01", id="month"),
            pytest.param(TimeGranularity.YEAR, "2025-01-01", "2026-01-01", id="year"),
        ],
    )
    def test_steps_exact_time_boundary(
        self, granularity: TimeGranularity, lower: str, upper: str
    ) -> None:
        """Step each time granularity to its exact next boundary."""
        start = PlainDateTime(2025, 1, 1, 12)
        spec = granularity.spec()
        assert start.format(spec.output_pattern) == lower
        assert spec.step(start).format(spec.output_pattern) == upper


class TestTimeBounds:
    """Time partition bounds behavior tests."""

    @pytest.mark.parametrize(
        ("granularity", "partition_id", "bounds"),
        [
            pytest.param(
                TimeGranularity.HOUR,
                "2025010112",
                ("2025-01-01 12:00:00", "2025-01-01 13:00:00"),
                id="hour",
            ),
            pytest.param(
                TimeGranularity.DAY, "20251231", ("2025-12-31", "2026-01-01"), id="day"
            ),
            pytest.param(
                TimeGranularity.MONTH,
                "202512",
                ("2025-12-01", "2026-01-01"),
                id="month",
            ),
            pytest.param(
                TimeGranularity.YEAR, "2025", ("2025-01-01", "2026-01-01"), id="year"
            ),
        ],
    )
    def test_returns_the_bounds_of_a_partition(
        self,
        granularity: TimeGranularity,
        partition_id: str,
        bounds: tuple[str, str],
    ) -> None:
        """Return the start and the exclusive end that one partition id covers."""
        normalizer = PartitionNormalizer(
            TimeConfig(field="ts", granularity=granularity), "p.d.t", "signature"
        )

        assert normalizer.time_bounds(partition_id, granularity) == bounds

    @pytest.mark.parametrize(
        ("granularity", "partition_id"),
        [
            pytest.param(TimeGranularity.HOUR, "20250101", id="hour-without-hour"),
            pytest.param(TimeGranularity.DAY, "2025-01-01", id="day-with-dashes"),
            pytest.param(TimeGranularity.MONTH, "202513", id="month-13"),
            pytest.param(TimeGranularity.YEAR, "20x5", id="year-with-letter"),
        ],
    )
    def test_rejects_a_malformed_partition_id(
        self, granularity: TimeGranularity, partition_id: str
    ) -> None:
        """Reject an id that does not match the granularity."""
        normalizer = PartitionNormalizer(
            TimeConfig(field="ts", granularity=granularity), "p.d.t", "signature"
        )

        with pytest.raises(ValueError, match=r"Invalid time partition ID"):
            normalizer.time_bounds(partition_id, granularity)


class TestTableModified:
    """Table modification time behavior tests."""

    @pytest.mark.parametrize(
        ("modified", "expected"),
        [
            pytest.param(
                datetime(2025, 1, 1, tzinfo=UTC), "1735689600000", id="whole-second"
            ),
            pytest.param(
                datetime(2025, 1, 1, 0, 0, 0, 123_000, tzinfo=UTC),
                "1735689600123",
                id="milliseconds",
            ),
        ],
    )
    async def test_returns_epoch_milliseconds(
        self, modified: datetime, expected: str
    ) -> None:
        """Return the modification time as epoch milliseconds in text."""
        conn = AsyncMock(spec=BigQuery)
        table = table_from_api(
            {"tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"}}
        )
        conn.get_table.return_value = table
        table._properties["lastModifiedTime"] = str(int(modified.timestamp() * 1000))

        assert await table_modified(conn, "p.d.t") == expected

    async def test_rejects_a_table_without_a_modification_time(self) -> None:
        """Fail when BigQuery reports no modification time."""
        conn = AsyncMock(spec=BigQuery)
        conn.get_table.return_value = table_from_api(
            {"tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"}}
        )

        with pytest.raises(ValueError, match="Missing BigQuery modification time"):
            await table_modified(conn, "p.d.t")


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
        with pytest.raises(ValueError, match="Incomplete range partition metadata"):
            range_config(metadata, "p.d.t")

    def test_rejects_non_string_range_field(self) -> None:
        """Reject non-string partition fields returned by the SDK."""
        metadata = RangePartitioning(
            field=123,
            range_=PartitionRange(start=0, end=10, interval=1),
        )

        with pytest.raises(ValueError, match="Incomplete range partition metadata"):
            range_config(metadata, "p.d.t")

    @pytest.mark.parametrize(
        ("start", "end", "interval"),
        [
            pytest.param(0, 10, 0, id="zero-interval"),
            pytest.param(0, 10, -1, id="negative-interval"),
            pytest.param(10, 10, 1, id="empty-range"),
            pytest.param(10, 0, 1, id="reversed-range"),
        ],
    )
    def test_rejects_invalid_range_metadata(
        self, start: int, end: int, interval: int
    ) -> None:
        """Reject range metadata with an invalid interval or bounds, and name the table."""
        metadata = RangePartitioning(
            field="id", range_=PartitionRange(start=start, end=end, interval=interval)
        )

        with pytest.raises(
            ValueError, match=r"Invalid range partition metadata: p\.d\.t"
        ):
            range_config(metadata, "p.d.t")


class TestTimeConfiguration:
    """Time partition configuration behavior tests."""

    @pytest.mark.parametrize("granularity", list(TimeGranularity), ids=str)
    def test_converts_valid_time_metadata(self, granularity: TimeGranularity) -> None:
        """Convert valid time metadata into application configuration."""
        metadata = TimePartitioning(type_=granularity.value, field="created_at")
        assert time_config(metadata, "p.d.t") == TimeConfig(
            field="created_at", granularity=granularity
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

    def test_rejects_non_string_time_field(self) -> None:
        """Reject a non-string time field returned by the SDK."""
        with pytest.raises(ValueError, match="field"):
            time_config(TimePartitioning(field=123), "p.d.t")

    @pytest.mark.parametrize("granularity", ["WEEK", "MINUTE", "UNKNOWN"])
    def test_rejects_unsupported_time_granularity(self, granularity: str) -> None:
        """Reject unsupported time partition granularities."""
        with pytest.raises(ValueError, match="Unsupported"):
            time_config(
                TimePartitioning(type_=granularity, field="created_at"), "p.d.t"
            )


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


class TestPhysicalPartitions:
    """Physical partition discovery behavior tests."""

    @pytest.mark.asyncio
    async def test_limits_time_partitions_to_latest_n(self, bigquery: BigQuery) -> None:
        """Keep only the latest time partitions and a stable table signature.

        A signature change forces a full rebuild of every synced table.
        """
        signature, partitions = await physical_partitions(
            bigquery,
            "test.dataset.time_day",
            "{}",
            n=2,
        )

        assert signature == (
            "bc77326b756fb34943a572b0fd2722497d8f20365cc522b66f44c9b0d826f890"
        )
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

    @pytest.mark.parametrize(
        ("granularity", "partition_id", "lower", "upper"),
        [
            (
                TimeGranularity.HOUR,
                "2025010112",
                "2025-01-01 12:00:00",
                "2025-01-01 13:00:00",
            ),
            (TimeGranularity.DAY, "20250101", "2025-01-01", "2025-01-02"),
            (TimeGranularity.MONTH, "202501", "2025-01-01", "2025-02-01"),
            (TimeGranularity.YEAR, "2025", "2025-01-01", "2026-01-01"),
        ],
        ids=["hour", "day", "month", "year"],
    )
    def test_normalizes_time_partition_to_exact_bounds(
        self, granularity: TimeGranularity, partition_id: str, lower: str, upper: str
    ) -> None:
        """Normalize a time partition ID into its exact half-open range."""
        partition = PartitionNormalizer(
            TimeConfig(field="created_at", granularity=granularity),
            "p.d.t",
            "signature",
        ).normalize(metadata_row(partition_id, 0))
        assert partition is not None
        assert isinstance(partition.selection, TimeRangeSelection)
        assert (partition.selection.lower, partition.selection.upper) == (lower, upper)

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
            ).normalize(metadata_row("0", 0, None))

    def test_skips_null_time_bucket(self) -> None:
        """Skip a null bucket for time partitioning."""
        partition = PartitionNormalizer(
            TimeConfig(field="created_at", granularity=TimeGranularity.DAY),
            "p.d.t",
            "signature",
        ).normalize(metadata_row("__NULL__", 0))
        assert partition is None
