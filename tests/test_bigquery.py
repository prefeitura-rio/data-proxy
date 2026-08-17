"""Tests for BigQuery table metadata helpers."""

from datetime import UTC, datetime

import pytest
from google.cloud.bigquery import PartitionRange, RangePartitioning
from helpers import FakeBigQueryClient, bigquery_client

from dp.bigquery import parse_table_reference, physical_partitions, table_modified

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)


def range_config(
    *, start: int | None = 0, end: int | None = 100, interval: int | None = 10
) -> RangePartitioning:
    """Return range metadata for one fake table."""
    return RangePartitioning(
        field="cpf",
        range_=PartitionRange(start=start, end=end, interval=interval),
    )


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


@pytest.mark.parametrize("value", ["p.d", "p.d.t.extra", "p.d.t; DROP TABLE x"])
def test_parse_table_reference_rejects_invalid_values(value: str) -> None:
    """Metadata identifiers must use one canonical project.dataset.table value."""
    with pytest.raises(ValueError, match="Invalid BigQuery table reference"):
        parse_table_reference(value)


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
        bigquery_client(fake), "p.d.t", '{"strategy":"all_with_partitions"}'
    )

    assert table_signature
    assert partitions["0"].model_dump(exclude={"signature"}) == {
        "partition_id": "0",
        "column": "cpf",
        "lower": 0,
        "upper": 10,
    }
    assert partitions["20"].upper == 25
    assert "INFORMATION_SCHEMA.PARTITIONS" in fake.query_calls[0]


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeBigQueryClient(), "requires a range-partitioned table"),
        (
            FakeBigQueryClient(range_partitioning=range_config(), table_type="VIEW"),
            "requires a range-partitioned table",
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
                rows=[{"partition_id": "__NULL__", "last_modified_time": MODIFIED}],
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
    ],
)
def test_physical_partitions_rejects_invalid_metadata(
    fake: FakeBigQueryClient, message: str
) -> None:
    """Invalid or incomplete physical metadata fails explicitly."""
    error = TypeError if "modification" in message else ValueError
    with pytest.raises(error, match=message):
        physical_partitions(bigquery_client(fake), "p.d.t", "{}")
