"""Tests for BigQuery table metadata helpers."""

from datetime import UTC, datetime
from typing import cast

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


class TestBigQueryTableModified:
    """Tests for TableModified behavior."""

    def test_table_modified_returns_epoch_milliseconds(self, bigquery: Client) -> None:
        """A real emulator metadata timestamp is normalized to epoch milliseconds."""
        bigquery.get_table("test.dataset.plain")

        result = table_modified(bigquery, "test.dataset.plain")

        assert result.isdigit()

    def test_table_modified_rejects_missing_timestamp(
        self,
        bigquery: Client,
    ) -> None:
        """Missing modification metadata fails instead of silently resyncing."""
        with pytest.raises(ValueError, match="Missing BigQuery modification time"):
            table_modified(bigquery, "test.dataset.missing_modified")


class TestBigQueryPhysicalPartitions:
    """Tests for PhysicalPartitions behavior."""

    def test_physical_partitions_normalizes_existing_range_buckets(
        self, bigquery: Client
    ) -> None:
        """BigQuery range metadata becomes generic lower and upper bounds."""
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
        """Range metadata with an explicit nonzero start remains unchanged."""
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
        """BigQuery's __NULL__ bucket becomes a remainder partition, not an error."""
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
        """DAY time partitions normalize raw partition ids into [start, end) ranges."""
        _, partitions = physical_partitions(bigquery, "test.dataset.time_day", "{}")

        assert partitions["20250101"].selection.model_dump() == {
            "type": "time_range",
            "column": "data_particao",
            "lower": "2025-01-01",
            "upper": "2025-01-02",
        }

    @pytest.mark.parametrize(
        ("type_", "partition_id", "lower", "upper"),
        [
            ("HOUR", "2025010112", "2025-01-01 12:00:00", "2025-01-01 13:00:00"),
            ("DAY", "20250101", "2025-01-01", "2025-01-02"),
            ("MONTH", "202512", "2025-12-01", "2026-01-01"),
            ("YEAR", "2025", "2025-01-01", "2026-01-01"),
        ],
    )
    def test_physical_partitions_normalizes_every_time_granularity(
        self, bigquery: Client, type_: str, partition_id: str, lower: str, upper: str
    ) -> None:
        """Every BigQuery time-partition granularity resolves correct [start, end) bounds."""
        table_name = {
            "HOUR": "time_hour",
            "DAY": "time_day",
            "MONTH": "time_month",
            "YEAR": "time_year",
        }[type_]
        _, partitions = physical_partitions(
            bigquery, f"test.dataset.{table_name}", "{}"
        )

        selection = partitions[partition_id].selection
        assert selection.model_dump() == {
            "type": "time_range",
            "column": "data_particao",
            "lower": lower,
            "upper": upper,
        }

    def test_physical_partitions_skips_time_null_bucket(self, bigquery: Client) -> None:
        """The __NULL__ bucket is meaningless for a time column and is skipped."""
        _, partitions = physical_partitions(
            bigquery, "test.dataset.time_day_skip", "{}"
        )

        assert set(partitions) == {"20250101"}

    def test_physical_partitions_keeps_last_n_time_partitions(
        self, bigquery: Client
    ) -> None:
        """n keeps only the highest n time partition ids."""
        _, partitions = physical_partitions(
            bigquery, "test.dataset.time_day", "{}", n=2
        )

        assert set(partitions) == {"20250102", "20250103"}

    def test_physical_partitions_rejects_n_for_range_partitioned_tables(
        self,
        bigquery: Client,
    ) -> None:
        """n only makes sense for time-partitioned tables."""
        with pytest.raises(
            ValueError, match="n is only supported for time-partitioned"
        ):
            physical_partitions(bigquery, "test.dataset.range_buckets", "{}", n=2)

    def test_physical_partitions_rejects_unsupported_time_granularity(
        self,
        bigquery: Client,
    ) -> None:
        """An unrecognized BigQuery time-partition granularity fails explicitly."""
        with pytest.raises(ValueError, match="Unsupported time partition granularity"):
            physical_partitions(bigquery, "test.dataset.time_week", "{}")

    def test_physical_partitions_rejects_ingestion_time_partitioning(
        self,
        bigquery: Client,
    ) -> None:
        """Ingestion-time partitioning without an explicit field is unsupported."""
        with pytest.raises(ValueError, match="Ingestion-time partitioning"):
            physical_partitions(bigquery, "test.dataset.time_ingestion", "{}")


class TestBigQuery:
    """Tests for BigQuery module behavior."""

    def test_parse_table_reference_returns_named_fields(
        self,
    ) -> None:
        """A valid reference exposes project, dataset, and table attributes."""
        reference = parse_table_reference("project.dataset.table-name")

        assert reference.project == "project"
        assert reference.dataset == "dataset"
        assert reference.table == "table-name"

    @pytest.mark.parametrize(
        ("field", "start", "end", "interval", "message"),
        [
            ("", 0, 10, 1, "field must not be empty"),
            ("id", 0, 10, 0, "interval must be positive"),
            ("id", 10, 10, 1, "start must precede end"),
        ],
    )
    def test_range_config_rejects_invalid_values(
        self, field: str, start: int, end: int, interval: int, message: str
    ) -> None:
        """Range configuration owns its value invariants."""
        with pytest.raises(ValueError, match=message):
            RangeConfig(field, start, end, interval)

    def test_time_config_rejects_empty_field(
        self,
    ) -> None:
        """Time configuration requires a partition field."""
        with pytest.raises(ValueError, match="field must not be empty"):
            TimeConfig("", TimeGranularity.DAY)

    @pytest.mark.parametrize(
        ("table", "message"),
        [
            ("plain", "requires a time- or range-partitioned table"),
            ("range_view", "requires a physically partitioned table"),
            ("range_missing_interval", "Incomplete range partition metadata"),
            ("range_zero_interval", "Invalid range partition metadata"),
            ("range_unpartitioned", "Unsupported BigQuery partition"),
            ("range_bad_id", "Invalid range partition ID"),
            ("range_misaligned_id", "Invalid range partition ID"),
            ("range_missing_partition_modified", "Missing partition modification time"),
            ("time_unpartitioned", "Unsupported BigQuery partition"),
            ("time_bad_id", "Invalid time partition ID"),
        ],
    )
    def test_physical_partitions_rejects_invalid_metadata(
        self,
        bigquery: Client,
        table: str,
        message: str,
    ) -> None:
        """Invalid or incomplete physical metadata fails explicitly."""
        with pytest.raises(
            TypeError if "modification" in message else ValueError,
            match=message,
        ):
            physical_partitions(bigquery, f"test.dataset.{table}", "{}")

    def test_normalize_partition_rejects_invalid_kind_config(
        self,
    ) -> None:
        """Verify normalize partition rejects invalid kind config."""
        with pytest.raises(AssertionError):
            normalize_partition(
                cast(
                    "Row",
                    cast(
                        object,
                        {"partition_id": "1", "last_modified_time": datetime.now(UTC)},
                    ),
                ),
                "p.d.t",
                cast("PartitionKindConfig", cast(object, "invalid")),
                "sig",
            )
