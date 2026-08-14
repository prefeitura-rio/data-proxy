"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import cast
from unittest.mock import patch

import pytest
from helpers import FakeDuckDBConnection
from psycopg import sql

from dp.extraction import build_columns, build_mapping, extract_task
from dp.models import SyncTask


def render(value: object) -> str:
    """Render a mapping value that is expected to be a psycopg Composable."""
    return cast("sql.Composable", value).as_string(None)


@pytest.mark.parametrize(
    ("json_columns", "expected"),
    [
        ([], "*"),
        (
            ["units", "data"],
            '* REPLACE (to_json("units") AS "units", to_json("data") AS "data")',
        ),
    ],
)
def test_build_columns(json_columns: list[str], expected: str) -> None:
    """STRUCT columns are replaced with JSON expressions; flat tables keep `*`."""
    assert build_columns(json_columns).as_string(None) == expected


@pytest.mark.parametrize(
    (
        "partition_column",
        "partition_value",
        "expected_path",
        "rendered_field",
        "expected_rendered",
    ),
    [
        (None, None, "duckdb/write_dump", "gcs_path", "'s3://b/t/data.parquet'"),
        (
            "dt",
            "2025-01-15",
            "duckdb/write_window",
            "partition_value",
            "'2025-01-15'",
        ),
    ],
)
def test_build_mapping_selects_template(
    partition_column: str | None,
    partition_value: str | None,
    expected_path: str,
    rendered_field: str,
    expected_rendered: str,
) -> None:
    """A task's partition fields select the dump or window template."""
    gcs_path = (
        f"s3://b/t/{partition_value}/data.parquet"
        if partition_value
        else "s3://b/t/data.parquet"
    )
    task = SyncTask(
        sync_id="s1",
        bq_table="p.d.t",
        gcs_path=gcs_path,
        partition_column=partition_column,
        partition_value=partition_value,
    )

    spec = build_mapping(task)

    assert spec["path"] == expected_path
    assert render(spec["mapping"][rendered_field]) == expected_rendered


def test_extract_task_executes_rendered_sql() -> None:
    """Extraction opens DuckDB and executes exactly one rendered statement."""
    db = FakeDuckDBConnection()
    task = SyncTask(sync_id="s1", bq_table="p.d.t", gcs_path="s3://b/t/data.parquet")

    with (
        patch("dp.extraction.connect", return_value=db),
        patch("dp.extraction.load_template", return_value="SELECT 1"),
    ):
        extract_task(task)

    assert db.executed == ["SELECT 1"]
