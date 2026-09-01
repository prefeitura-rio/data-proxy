"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import cast
from unittest.mock import patch

import pytest
from duckdb import DuckDBPyConnection
from psycopg.sql import Composable

from dp.extraction import build_columns, build_mapping, extract_task, selection_fields
from dp.models import (
    AllSelection,
    DumpTask,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)


def render(value: object) -> str:
    """Render a mapping value that is expected to be a psycopg Composable."""
    return cast("Composable", value).as_string(None)


def test_selection_fields_rejects_unknown_selection() -> None:
    """Unknown task selections fail instead of producing incomplete SQL."""
    with pytest.raises(AssertionError):
        selection_fields(cast("TaskSelection", object()))


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
            TimeRangeSelection(column="dt", lower="2025-01-15", upper="2025-01-16"),
            "duckdb/write_partition",
            "upper",
            "'2025-01-16'",
        ),
        (
            RangeSelection(partition_id="10", column="cpf", lower=10, upper=20),
            "duckdb/write_partition",
            "upper",
            "20",
        ),
        (
            RemainderSelection(column="cpf", start=0, end=100),
            "duckdb/write_remainder",
            "upper",
            "100",
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
    task = DumpTask(
        run_id="s1",
        table="p.d.t",
        bucket_path="s3://b/t/data.parquet",
        selection=selection,
    )

    spec = build_mapping(task)

    assert spec.path == expected_path
    assert render(spec.mapping[rendered_field]) == expected_rendered


def test_extract_task_executes_rendered_sql(duckdb: DuckDBPyConnection) -> None:
    """Extraction executes one rendered statement through DuckDB."""
    db = duckdb
    task = DumpTask(
        run_id="s1",
        table="p.d.t",
        bucket_path="s3://b/t/data.parquet",
        selection=AllSelection(),
    )

    with patch("dp.extraction.load_template", return_value="SELECT 1"):
        extract_task(task, db)

    assert db.execute("SELECT 1").fetchone() == (1,)


def test_build_mapping_rejects_invalid_selection() -> None:

    # The exhaustive branch is defensive; use an invalid runtime value.
    invalid = type(
        "InvalidTask",
        (),
        {
            "table": "p.d.t",
            "bucket_path": "s3://b",
            "json_columns": [],
            "selection": object(),
        },
    )()
    with (
        patch("dp.extraction.selection_fields", return_value={}),
        pytest.raises(AssertionError),
    ):
        build_mapping(cast(DumpTask, cast(object, invalid)))
