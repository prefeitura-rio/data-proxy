"""Tests for BigQuery table metadata helpers."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from google.cloud.bigquery import (
    Client,
    PartitionRange,
    RangePartitioning,
    Row,
    Table,
    TimePartitioning,
)

from dp.bigquery import (
    PartitionKindConfig,
    RangeConfig,
    TimeConfig,
    TimeGranularity,
    bigquery_clients,
    normalize_partition,
    parse_table_reference,
    physical_partitions,
    table_modified,
)
from dp.models import RangeSelection

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)


def preseeded_table(client: Client, name: str) -> Table:
    """Return one table from the canonical BigQuery CSV dataset."""
    return client.get_table(f"test.dataset.{name}")


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


def test_bigquery_clients_reuses_and_closes_clients() -> None:
    first_close = MagicMock()
    second_close = MagicMock()
    first_mock = MagicMock(close=first_close)
    second_mock = MagicMock(close=second_close)
    with patch("dp.bigquery.Client", side_effect=[first_mock, second_mock]) as client:
        with bigquery_clients() as get_client:
            first = get_client("p")
            assert get_client("p") is first
            get_client("q")
        assert client.call_count == 2
        first_close.assert_called_once()
        second_close.assert_called_once()


def test_table_modified_returns_epoch_milliseconds(bigquery: Client) -> None:
    """A real emulator metadata timestamp is normalized to epoch milliseconds."""
    preseeded_table(bigquery, "plain")

    result = table_modified(bigquery, "test.dataset.plain")

    assert result.isdigit()


def test_table_modified_rejects_missing_timestamp() -> None:
    """Missing modification metadata fails instead of silently resyncing."""
    client = MagicMock(spec=Client)
    client.get_table.return_value.modified = None

    with pytest.raises(ValueError, match="Missing BigQuery modification time"):
        table_modified(client, "p.d.t")


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


def test_physical_partitions_normalizes_existing_range_buckets(
    bigquery: Client,
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
    bigquery: Client,
) -> None:
    """Range metadata with an explicit nonzero start remains unchanged."""
    _, partitions = physical_partitions(bigquery, "test.dataset.range_start_five", "{}")

    selection = partitions["5"].selection
    assert isinstance(selection, RangeSelection)
    assert selection.lower == 5


def test_physical_partitions_normalizes_null_bucket_into_remainder(
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
    bigquery: Client, type_: str, partition_id: str, lower: str, upper: str
) -> None:
    """Every BigQuery time-partition granularity resolves correct [start, end) bounds."""
    table_name = {
        "HOUR": "time_hour",
        "DAY": "time_day",
        "MONTH": "time_month",
        "YEAR": "time_year",
    }[type_]
    _, partitions = physical_partitions(bigquery, f"test.dataset.{table_name}", "{}")

    selection = partitions[partition_id].selection
    assert selection.model_dump() == {
        "type": "time_range",
        "column": "data_particao",
        "lower": lower,
        "upper": upper,
    }


def test_physical_partitions_skips_time_null_bucket(bigquery: Client) -> None:
    """The __NULL__ bucket is meaningless for a time column and is skipped."""
    _, partitions = physical_partitions(bigquery, "test.dataset.time_day_skip", "{}")

    assert set(partitions) == {"20250101"}


def test_physical_partitions_keeps_last_n_time_partitions(bigquery: Client) -> None:
    """n keeps only the highest n time partition ids."""
    _, partitions = physical_partitions(bigquery, "test.dataset.time_day", "{}", n=2)

    assert set(partitions) == {"20250102", "20250103"}


def test_physical_partitions_rejects_n_for_range_partitioned_tables(
    bigquery: Client,
) -> None:
    """n only makes sense for time-partitioned tables."""
    with pytest.raises(ValueError, match="n is only supported for time-partitioned"):
        physical_partitions(bigquery, "test.dataset.range_buckets", "{}", n=2)


def test_physical_partitions_rejects_unsupported_time_granularity() -> None:
    """An unrecognized BigQuery time-partition granularity fails explicitly."""
    client = MagicMock(spec=Client)
    client.get_table.return_value = MagicMock(
        range_partitioning=None,
        time_partitioning=time_config(type_="WEEK"),
        table_type="TABLE",
    )

    with pytest.raises(ValueError, match="Unsupported time partition granularity"):
        physical_partitions(client, "p.d.t", "{}")


def test_physical_partitions_rejects_ingestion_time_partitioning() -> None:
    """Ingestion-time partitioning without an explicit field is unsupported."""
    client = MagicMock(spec=Client)
    client.get_table.return_value = MagicMock(
        range_partitioning=None,
        time_partitioning=time_config(field=None),
        table_type="TABLE",
    )

    with pytest.raises(ValueError, match="Ingestion-time partitioning"):
        physical_partitions(client, "p.d.t", "{}")


def _mock_client(
    *,
    range_partitioning: RangePartitioning | None = None,
    time_partitioning: TimePartitioning | None = None,
    table_type: str = "TABLE",
    rows: list[dict[str, object]] | None = None,
) -> Client:
    client = MagicMock(spec=Client)
    client.get_table.return_value = MagicMock(
        range_partitioning=range_partitioning,
        time_partitioning=time_partitioning,
        table_type=table_type,
    )
    client.query.return_value.result.return_value = rows or []
    return cast(Client, client)


@pytest.mark.parametrize(
    ("client", "message"),
    [
        (_mock_client(), "requires a time- or range-partitioned table"),
        (
            _mock_client(range_partitioning=range_config(), table_type="VIEW"),
            "requires a physically partitioned table",
        ),
        (
            _mock_client(range_partitioning=range_config(interval=None)),
            "Incomplete range partition metadata",
        ),
        (
            _mock_client(range_partitioning=range_config(interval=0)),
            "Invalid range partition metadata",
        ),
        (
            _mock_client(
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
            _mock_client(
                range_partitioning=range_config(),
                rows=[{"partition_id": "bad", "last_modified_time": MODIFIED}],
            ),
            "Invalid range partition ID",
        ),
        (
            _mock_client(
                range_partitioning=range_config(),
                rows=[{"partition_id": "5", "last_modified_time": MODIFIED}],
            ),
            "Invalid range partition ID",
        ),
        (
            _mock_client(
                range_partitioning=range_config(),
                rows=[{"partition_id": "0", "last_modified_time": None}],
            ),
            "Missing partition modification time",
        ),
        (
            _mock_client(
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
            _mock_client(
                time_partitioning=time_config(),
                rows=[{"partition_id": "bad", "last_modified_time": MODIFIED}],
            ),
            "Invalid time partition ID",
        ),
    ],
)
def test_physical_partitions_rejects_invalid_metadata(
    client: Client, message: str
) -> None:
    """Invalid or incomplete physical metadata fails explicitly."""
    error = TypeError if "modification" in message else ValueError
    with pytest.raises(error, match=message):
        physical_partitions(client, "p.d.t", "{}")


def test_normalize_partition_rejects_invalid_kind_config() -> None:

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
