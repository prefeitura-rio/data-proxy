"""Tests for BigQuery table metadata helpers."""

from dataclasses import dataclass

import pytest
from google.cloud.bigquery import Client, Row

from dp.bigquery.config import (
    PartitionKindConfig,
    RangeConfig,
    TimeConfig,
    TimeGranularity,
)
from dp.bigquery.partitions import normalize_partition, physical_partitions
from dp.bigquery.tables import parse_table_reference, table_modified
from dp.models import RangeSelection


@dataclass(frozen=True, slots=True)
class RangeConfigCase:
    """One invalid range configuration case for parametrized testing."""

    field: str
    start: int
    end: int
    interval: int
    message: str


@dataclass(frozen=True, slots=True)
class TimeGranularityCase:
    """One time-partition granularity case for parametrized testing."""

    type: str
    partition_id: str
    lower: str
    upper: str


@dataclass(frozen=True, slots=True)
class InvalidMetadataCase:
    """One invalid metadata case for parametrized testing."""

    table: str
    message: str


class TestBigQueryTableModified:
    """Tests for TableModified behavior."""

    def test_table_modified_returns_epoch_milliseconds(self, bigquery: Client) -> None:
        """
        GIVEN: a table with a real metadata timestamp.
        WHEN: table_modified is called.
        THEN: it returns the timestamp as epoch milliseconds.
        """
        bigquery.get_table("test.dataset.plain")

        result = table_modified(bigquery, "test.dataset.plain")

        assert result.isdigit()

    def test_table_modified_rejects_missing_timestamp(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a table without modification metadata.
        WHEN: table_modified is called.
        THEN: it raises ValueError instead of silently resyncing.
        """
        with pytest.raises(ValueError, match="Missing BigQuery modification time"):
            table_modified(bigquery, "test.dataset.missing_modified")


class TestBigQueryPhysicalPartitions:
    """Tests for PhysicalPartitions behavior."""

    def test_physical_partitions_normalizes_existing_range_buckets(
        self, bigquery: Client
    ) -> None:
        """
        GIVEN: a range-partitioned table with aligned buckets.
        WHEN: physical_partitions is called.
        THEN: range metadata becomes generic lower and upper bounds.
        """
        table_signature, partitions = physical_partitions(
            bigquery, "test.dataset.range_buckets", '{"strategy":"partitioned"}'
        )

        assert table_signature
        assert partitions["0"].model_dump(exclude={"signature"}) == {
            "partition_id": "0",
            "selection": {
                "type": "range",
                "partition_id": "0",
                "column": "cpf",
                "lower": 0,
                "upper": 10,
            },
        }
        upper_selection = partitions["20"].selection
        assert isinstance(upper_selection, RangeSelection)
        assert upper_selection.upper == 25

    def test_physical_partitions_accepts_nonzero_range_start(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: range metadata with an explicit nonzero start.
        WHEN: physical_partitions is called.
        THEN: the start remains unchanged.
        """
        _, partitions = physical_partitions(
            bigquery, "test.dataset.range_start_five", "{}"
        )

        selection = partitions["5"].selection
        assert isinstance(selection, RangeSelection)
        assert selection.lower == 5

    def test_physical_partitions_normalizes_null_bucket_into_remainder(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a range-partitioned table with a __NULL__ bucket.
        WHEN: physical_partitions is called.
        THEN: the null bucket becomes a remainder partition.
        """
        _, partitions = physical_partitions(bigquery, "test.dataset.range_null", "{}")

        remainder = partitions["__NULL__"].selection
        assert remainder.type == "remainder"
        assert remainder.column == "cpf"
        assert remainder.start == 0
        assert remainder.end == 25

    def test_physical_partitions_normalizes_time_partitions_into_ranges(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a DAY time-partitioned table.
        WHEN: physical_partitions is called.
        THEN: raw partition ids normalize into [start, end) date ranges.
        """
        _, partitions = physical_partitions(bigquery, "test.dataset.time_day", "{}")

        assert partitions["20250101"].selection.model_dump() == {
            "type": "time_range",
            "column": "data_particao",
            "lower": "2025-01-01",
            "upper": "2025-01-02",
        }

    @pytest.mark.parametrize(
        "case",
        [
            TimeGranularityCase(
                "HOUR", "2025010112", "2025-01-01 12:00:00", "2025-01-01 13:00:00"
            ),
            TimeGranularityCase("DAY", "20250101", "2025-01-01", "2025-01-02"),
            TimeGranularityCase("MONTH", "202512", "2025-12-01", "2026-01-01"),
            TimeGranularityCase("YEAR", "2025", "2025-01-01", "2026-01-01"),
        ],
        ids=lambda case: case.type,
    )
    def test_physical_partitions_normalizes_every_time_granularity(
        self, bigquery: Client, case: TimeGranularityCase
    ) -> None:
        """
        GIVEN: a time-partitioned table for each supported granularity.
        WHEN: physical_partitions is called.
        THEN: each partition id resolves to its correct [start, end) bounds.
        """
        table_name = {
            "HOUR": "time_hour",
            "DAY": "time_day",
            "MONTH": "time_month",
            "YEAR": "time_year",
        }[case.type]

        _, partitions = physical_partitions(
            bigquery, f"test.dataset.{table_name}", "{}"
        )

        selection = partitions[case.partition_id].selection
        assert selection.model_dump() == {
            "type": "time_range",
            "column": "data_particao",
            "lower": case.lower,
            "upper": case.upper,
        }

    def test_physical_partitions_skips_time_null_bucket(self, bigquery: Client) -> None:
        """
        GIVEN: a time-partitioned table with a __NULL__ bucket.
        WHEN: physical_partitions is called.
        THEN: the null bucket is skipped.
        """
        _, partitions = physical_partitions(
            bigquery, "test.dataset.time_day_skip", "{}"
        )

        assert set(partitions) == {"20250101"}

    def test_physical_partitions_keeps_last_n_time_partitions(
        self, bigquery: Client
    ) -> None:
        """
        GIVEN: a time-partitioned table with more than n partitions.
        WHEN: physical_partitions is called with n=2.
        THEN: only the highest n partition ids are kept.
        """
        _, partitions = physical_partitions(
            bigquery, "test.dataset.time_day", "{}", n=2
        )

        assert set(partitions) == {"20250102", "20250103"}

    def test_physical_partitions_rejects_n_for_range_partitioned_tables(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a range-partitioned table.
        WHEN: physical_partitions is called with n=2.
        THEN: it raises ValueError because n only supports time partitions.
        """
        with pytest.raises(
            ValueError, match="n is only supported for time-partitioned"
        ):
            physical_partitions(bigquery, "test.dataset.range_buckets", "{}", n=2)

    def test_physical_partitions_rejects_unsupported_time_granularity(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a table with an unrecognized time partition granularity.
        WHEN: physical_partitions is called.
        THEN: it raises ValueError.
        """
        with pytest.raises(ValueError, match="Unsupported time partition granularity"):
            physical_partitions(bigquery, "test.dataset.time_week", "{}")

    def test_physical_partitions_rejects_ingestion_time_partitioning(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a table with ingestion-time partitioning and no explicit field.
        WHEN: physical_partitions is called.
        THEN: it raises ValueError.
        """
        with pytest.raises(ValueError, match="Ingestion-time partitioning"):
            physical_partitions(bigquery, "test.dataset.time_ingestion", "{}")


class TestBigQuery:
    """Tests for BigQuery module behavior."""

    def test_parse_table_reference_returns_named_fields(
        self,
    ) -> None:
        """
        GIVEN: a valid project.dataset.table reference.
        WHEN: parse_table_reference is called.
        THEN: it exposes project, dataset, and table attributes.
        """
        reference = parse_table_reference("project.dataset.table-name")

        assert reference.project == "project"
        assert reference.dataset == "dataset"
        assert reference.table == "table-name"

    @pytest.mark.parametrize(
        "case",
        [
            RangeConfigCase("", 0, 10, 1, "field must not be empty"),
            RangeConfigCase("id", 0, 10, 0, "interval must be positive"),
            RangeConfigCase("id", 10, 10, 1, "start must precede end"),
        ],
        ids=lambda case: case.message,
    )
    def test_range_config_rejects_invalid_field_interval_and_bounds(
        self, case: RangeConfigCase
    ) -> None:
        """
        GIVEN: invalid range configuration values.
        WHEN: RangeConfig is constructed.
        THEN: it raises ValueError with the invariant message.
        """
        with pytest.raises(ValueError, match=case.message):
            RangeConfig(
                case.field,
                case.start,
                case.end,
                case.interval,
            )

    def test_time_config_rejects_empty_field(
        self,
    ) -> None:
        """
        GIVEN: an empty partition field.
        WHEN: TimeConfig is constructed.
        THEN: it raises ValueError.
        """
        with pytest.raises(ValueError, match="field must not be empty"):
            TimeConfig("", TimeGranularity.DAY)

    @pytest.mark.parametrize(
        "case",
        [
            InvalidMetadataCase("plain", "requires a time- or range-partitioned table"),
            InvalidMetadataCase(
                "range_view", "requires a physically partitioned table"
            ),
            InvalidMetadataCase(
                "range_missing_interval", "Incomplete range partition metadata"
            ),
            InvalidMetadataCase(
                "range_zero_interval", "Invalid range partition metadata"
            ),
            InvalidMetadataCase(
                "range_unpartitioned", "Unsupported BigQuery partition"
            ),
            InvalidMetadataCase("range_bad_id", "Invalid range partition ID"),
            InvalidMetadataCase("range_misaligned_id", "Invalid range partition ID"),
            InvalidMetadataCase(
                "range_missing_partition_modified",
                "Missing partition modification time",
            ),
            InvalidMetadataCase("time_unpartitioned", "Unsupported BigQuery partition"),
            InvalidMetadataCase("time_bad_id", "Invalid time partition ID"),
        ],
        ids=lambda case: case.table,
    )
    def test_physical_partitions_rejects_invalid_metadata_cases(
        self, bigquery: Client, case: InvalidMetadataCase
    ) -> None:
        """
        GIVEN: invalid or incomplete physical partition metadata.
        WHEN: physical_partitions is called.
        THEN: it raises ValueError or TypeError with an explicit message.
        """
        with pytest.raises(
            TypeError if "modification" in case.message else ValueError,
            match=case.message,
        ):
            physical_partitions(bigquery, f"test.dataset.{case.table}", "{}")

    def test_normalize_partition_rejects_invalid_kind_config(
        self,
        invalid_partition_row: Row,
        invalid_kind_config: PartitionKindConfig,
    ) -> None:
        """
        GIVEN: an invalid partition kind config.
        WHEN: normalize_partition is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            normalize_partition(
                invalid_partition_row,
                "p.d.t",
                invalid_kind_config,
                "sig",
            )
