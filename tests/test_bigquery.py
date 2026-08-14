"""Tests for BigQuery table metadata helpers."""

from datetime import UTC, datetime

import pytest
from helpers import FakeBigQueryClient, bigquery_client

from dp.bigquery import table_modified


def test_table_modified_returns_epoch_milliseconds() -> None:
    """A metadata timestamp is normalized to epoch milliseconds."""
    fake = FakeBigQueryClient(datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC))

    result = table_modified(bigquery_client(fake), "p.d.t")

    assert result == "1786106872683"
    assert fake.calls == ["p.d.t"]


def test_table_modified_rejects_missing_timestamp() -> None:
    """Missing modification metadata fails instead of silently resyncing."""
    fake = FakeBigQueryClient(None)

    with pytest.raises(ValueError, match="Missing BigQuery modification time"):
        table_modified(bigquery_client(fake), "p.d.t")
