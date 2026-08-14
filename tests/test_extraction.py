"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import cast
from unittest.mock import patch

from helpers import FakeDuckDBConnection
from psycopg import sql

from dp.extraction import build_columns, build_mapping, extract_task
from dp.models import SyncTask


def render(value: object) -> str:
    """Render a mapping value that is expected to be a psycopg Composable."""
    return cast("sql.Composable", value).as_string(None)


def test_build_columns_returns_star_for_flat_table() -> None:
    """Flat tables retain all columns without replacement expressions."""
    assert build_columns([]).as_string(None) == "*"


def test_build_columns_converts_structs_to_json() -> None:
    """STRUCT columns are replaced with JSON expressions."""
    assert build_columns(["units", "data"]).as_string(None) == (
        '* REPLACE (to_json("units") AS "units", to_json("data") AS "data")'
    )


def test_build_mapping_selects_dump_template() -> None:
    """A task without a partition uses the dump extraction template."""
    task = SyncTask(sync_id="s1", bq_table="p.d.t", gcs_path="s3://b/t/data.parquet")

    spec = build_mapping(task)

    assert spec["path"] == "duckdb/write_dump"
    assert render(spec["mapping"]["gcs_path"]) == "'s3://b/t/data.parquet'"


def test_build_mapping_selects_window_template() -> None:
    """A partition value uses the window extraction template."""
    task = SyncTask(
        sync_id="s1",
        bq_table="p.d.t",
        gcs_path="s3://b/t/2025-01-15/data.parquet",
        partition_column="dt",
        partition_value="2025-01-15",
    )

    spec = build_mapping(task)

    assert spec["path"] == "duckdb/write_window"
    assert render(spec["mapping"]["partition_value"]) == "'2025-01-15'"


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
