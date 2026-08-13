"""Tests for BigQuery table metadata helpers."""

from datetime import UTC, datetime
from typing import cast

import pytest
from google.cloud import bigquery

from dp.bigquery import table_modified


class FakeTable:
    """Minimal BigQuery table metadata response."""

    modified: datetime | None

    def __init__(self, modified: datetime | None) -> None:
        """Store a configurable modification timestamp."""
        self.modified = modified


class FakeClient:
    """Minimal metadata client that exposes get_table only."""

    table: FakeTable
    calls: list[str]

    def __init__(self, table: FakeTable) -> None:
        """Initialize metadata and call tracking."""
        self.table = table
        self.calls = []

    def get_table(self, bq_table: str) -> FakeTable:
        """Return configured metadata and record the table reference."""
        self.calls.append(bq_table)
        return self.table


def bigquery_client(fake: FakeClient) -> bigquery.Client:
    """Cast the metadata test double to the BigQuery client type."""
    return cast(bigquery.Client, cast(object, fake))


def test_table_modified_returns_epoch_milliseconds() -> None:
    """A metadata timestamp is normalized to epoch milliseconds."""
    fake = FakeClient(FakeTable(datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)))

    result = table_modified(bigquery_client(fake), "p.d.t")

    assert result == "1786106872683"
    assert fake.calls == ["p.d.t"]


def test_table_modified_rejects_missing_timestamp() -> None:
    """Missing modification metadata fails instead of silently resyncing."""
    fake = FakeClient(FakeTable(None))

    with pytest.raises(ValueError, match="Missing BigQuery modification time"):
        table_modified(bigquery_client(fake), "p.d.t")
