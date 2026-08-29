"""Tests for BigQuery table metadata helpers."""

from datetime import UTC, datetime

import pytest
from google.cloud.bigquery import PartitionRange, RangePartitioning, TimePartitioning
from helpers import FakeBigQueryClient, bigquery_client

from dp.bigquery import (
    RangeConfig,
    TimeConfig,
    TimeGranularity,
    parse_table_reference,
    physical_partitions,
    table_modified,
)
from dp.models import RangeSelection

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)


def range_config(
    *, start: int | None = 0, end: int | None = 100, interval: int | None = 10
) -> RangePartitioning:
    """Return range metadata for one fake table."""
    return RangePartitioning(
        field="cpf",
        range_=PartitionRange(start=start, end=end, interval=interval),
    )


def time_config(
    *, field: str | None = "data_particao", type_: str = "DAY"
) -> TimePartitioning:
    """Return time metadata for one fake table."""
    return TimePartitioning(field=field, type_=type_)


def test_table_modified_returns_epoch_milliseconds() -> None:
    """A metadata timestamp is normalized to epoch milliseconds."""
    fake = FakeBigQueryClient(MODIFIED)

    result = table_modified(bigquery_client(fake), "p.d.t")

    assert result == "1786106872683"
    assert fake.calls == ["p.d.t"]


def test_table_modified_rejects_missing_timestamp() -> None:
    """Missing modification metadata fails instead of silently resyncing."""
    fake = FakeBigQueryClient(None)

    with pytest.raises(ValueError, match="Missing BigQuery modification time"):
        table_modified(bigquery_client(fake), "p.d.t")


def test_parse_table_reference_returns_named_fields() -> None:
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
    field: str, start: int, end: int, interval: int, message: str
) -> None:
    """Range configuration owns its value invariants."""
    with pytest.raises(ValueError, match=message):
        RangeConfig(field, start, end, interval)


def test_time_config_rejects_empty_field() -> None:
    """Time configuration requires a partition field."""
    with pytest.raises(ValueError, match="field must not be empty"):
        TimeConfig("", TimeGranularity.DAY)


def test_physical_partitions_normalizes_existing_range_buckets() -> None:
    """BigQuery range metadata becomes generic lower and upper bounds."""
    fake = FakeBigQueryClient(
        range_partitioning=range_config(end=25),
        rows=[
            {"partition_id": "0", "last_modified_time": MODIFIED},
            {"partition_id": "20", "last_modified_time": MODIFIED},
        ],
    )

    table_signature, partitions = physical_partitions(
        bigquery_client(fake), "p.d.t", '{"strategy":"partitioned"}'
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
    assert "INFORMATION_SCHEMA.PARTITIONS" in fake.query_calls[0]


def test_physical_partitions_normalizes_null_bucket_into_remainder() -> None:
    """BigQuery's __NULL__ bucket becomes a remainder partition, not an error."""
    fake = FakeBigQueryClient(
        range_partitioning=range_config(end=25),
        rows=[{"partition_id": "__NULL__", "last_modified_time": MODIFIED}],
    )

    _, partitions = physical_partitions(bigquery_client(fake), "p.d.t", "{}")

    remainder = partitions["__NULL__"].selection
    assert remainder.type == "remainder"
    assert remainder.column == "cpf"
    assert remainder.start == 0
    assert remainder.end == 25


def test_physical_partitions_normalizes_time_partitions_into_ranges() -> None:
    """DAY time partitions normalize raw partition ids into [start, end) ranges."""
    fake = FakeBigQueryClient(
        time_partitioning=time_config(),
        rows=[
            {"partition_id": "20250101", "last_modified_time": MODIFIED},
            {"partition_id": "20250102", "last_modified_time": MODIFIED},
        ],
    )

    _, partitions = physical_partitions(bigquery_client(fake), "p.d.t", "{}")

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
    type_: str, partition_id: str, lower: str, upper: str
) -> None:
    """Every BigQuery time-partition granularity resolves correct [start, end) bounds."""
    fake = FakeBigQueryClient(
        time_partitioning=time_config(type_=type_),
        rows=[{"partition_id": partition_id, "last_modified_time": MODIFIED}],
    )

    _, partitions = physical_partitions(bigquery_client(fake), "p.d.t", "{}")

    selection = partitions[partition_id].selection
    assert selection.model_dump() == {
        "type": "time_range",
        "column": "data_particao",
        "lower": lower,
        "upper": upper,
    }


def test_physical_partitions_skips_time_null_bucket() -> None:
    """The __NULL__ bucket is meaningless for a time column and is skipped."""
    fake = FakeBigQueryClient(
        time_partitioning=time_config(),
        rows=[
            {"partition_id": "__NULL__", "last_modified_time": MODIFIED},
            {"partition_id": "20250101", "last_modified_time": MODIFIED},
        ],
    )

    _, partitions = physical_partitions(bigquery_client(fake), "p.d.t", "{}")

    assert set(partitions) == {"20250101"}


def test_physical_partitions_keeps_last_n_time_partitions() -> None:
    """n keeps only the highest n time partition ids."""
    fake = FakeBigQueryClient(
        time_partitioning=time_config(),
        rows=[
            {"partition_id": pid, "last_modified_time": MODIFIED}
            for pid in ("20250101", "20250102", "20250103")
        ],
    )

    _, partitions = physical_partitions(bigquery_client(fake), "p.d.t", "{}", n=2)

    assert set(partitions) == {"20250102", "20250103"}


def test_physical_partitions_rejects_n_for_range_partitioned_tables() -> None:
    """n only makes sense for time-partitioned tables."""
    fake = FakeBigQueryClient(range_partitioning=range_config())

    with pytest.raises(ValueError, match="n is only supported for time-partitioned"):
        physical_partitions(bigquery_client(fake), "p.d.t", "{}", n=2)


def test_physical_partitions_rejects_unsupported_time_granularity() -> None:
    """An unrecognized BigQuery time-partition granularity fails explicitly."""
    fake = FakeBigQueryClient(time_partitioning=time_config(type_="WEEK"))

    with pytest.raises(ValueError, match="Unsupported time partition granularity"):
        physical_partitions(bigquery_client(fake), "p.d.t", "{}")


def test_physical_partitions_rejects_ingestion_time_partitioning() -> None:
    """Ingestion-time partitioning without an explicit field is unsupported."""
    fake = FakeBigQueryClient(time_partitioning=time_config(field=None))

    with pytest.raises(ValueError, match="Ingestion-time partitioning"):
        physical_partitions(bigquery_client(fake), "p.d.t", "{}")


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeBigQueryClient(), "requires a time- or range-partitioned table"),
        (
            FakeBigQueryClient(range_partitioning=range_config(), table_type="VIEW"),
            "requires a physically partitioned table",
        ),
        (
            FakeBigQueryClient(range_partitioning=range_config(interval=None)),
            "Incomplete range partition metadata",
        ),
        (
            FakeBigQueryClient(range_partitioning=range_config(interval=0)),
            "Invalid range partition metadata",
        ),
        (
            FakeBigQueryClient(
                range_partitioning=range_config(),
                rows=[
                    {
                        "partition_id": "__UNPARTITIONED__",
                        "last_modified_time": MODIFIED,
                    }
                ],
            ),
            "Unsupported BigQuery partition",
        ),
        (
            FakeBigQueryClient(
                range_partitioning=range_config(),
                rows=[{"partition_id": "bad", "last_modified_time": MODIFIED}],
            ),
            "Invalid range partition ID",
        ),
        (
            FakeBigQueryClient(
                range_partitioning=range_config(),
                rows=[{"partition_id": "5", "last_modified_time": MODIFIED}],
            ),
            "Invalid range partition ID",
        ),
        (
            FakeBigQueryClient(
                range_partitioning=range_config(),
                rows=[{"partition_id": "0", "last_modified_time": None}],
            ),
            "Missing partition modification time",
        ),
        (
            FakeBigQueryClient(
                time_partitioning=time_config(),
                rows=[
                    {
                        "partition_id": "__UNPARTITIONED__",
                        "last_modified_time": MODIFIED,
                    }
                ],
            ),
            "Unsupported BigQuery partition",
        ),
        (
            FakeBigQueryClient(
                time_partitioning=time_config(),
                rows=[{"partition_id": "bad", "last_modified_time": MODIFIED}],
            ),
            "Invalid time partition ID",
        ),
    ],
)
def test_physical_partitions_rejects_invalid_metadata(
    fake: FakeBigQueryClient, message: str
) -> None:
    """Invalid or incomplete physical metadata fails explicitly."""
    error = TypeError if "modification" in message else ValueError
    with pytest.raises(error, match=message):
        physical_partitions(bigquery_client(fake), "p.d.t", "{}")
