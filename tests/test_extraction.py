"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import cast
from unittest.mock import patch

import pytest
from helpers import FakeDuckDBConnection
from psycopg.sql import Composable

from dp.extraction import build_columns, build_mapping, extract_task
from dp.models import (
    AllSelection,
    RangeSelection,
    SyncTask,
    TaskSelection,
    ValueSelection,
)


def render(value: object) -> str:
    """Render a mapping value that is expected to be a psycopg Composable."""
    return cast("Composable", value).as_string(None)


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
    ("selection", "expected_path", "rendered_field", "expected_rendered"),
    [
        (AllSelection(), "duckdb/write_all", "gcs_path", "'s3://b/t/data.parquet'"),
        (
            ValueSelection(column="dt", value="2025-01-15"),
            "duckdb/write_window",
            "partition_value",
            "'2025-01-15'",
        ),
        (
            RangeSelection(partition_id="10", column="cpf", lower=10, upper=20),
            "duckdb/write_partition",
            "partition_upper",
            "20",
        ),
    ],
)
def test_build_mapping_selects_template(
    selection: TaskSelection,
    expected_path: str,
    rendered_field: str,
    expected_rendered: str,
) -> None:
    """A task's discriminated selection chooses its extraction template."""
    task = SyncTask(
        sync_id="s1",
        bq_table="p.d.t",
        gcs_path="s3://b/t/data.parquet",
        selection=selection,
    )

    spec = build_mapping(task)

    assert spec["path"] == expected_path
    assert render(spec["mapping"][rendered_field]) == expected_rendered


def test_extract_task_executes_rendered_sql() -> None:
    """Extraction opens DuckDB and executes exactly one rendered statement."""
    db = FakeDuckDBConnection()
    task = SyncTask(
        sync_id="s1",
        bq_table="p.d.t",
        gcs_path="s3://b/t/data.parquet",
        selection=AllSelection(),
    )

    with (
        patch("dp.extraction.connect", return_value=db),
        patch("dp.extraction.load_template", return_value="SELECT 1"),
    ):
        extract_task(task)

    assert db.executed == ["SELECT 1"]
