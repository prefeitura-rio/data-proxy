"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import cast
from unittest.mock import patch

import pytest
from duckdb import DuckDBPyConnection

from dp.extraction import build_columns, build_mapping, extract_task, selection_fields
from dp.models import (
    AllSelection,
    DumpTask,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from tests.helpers import render


class TestExtraction:
    """Tests for extraction selection and SQL behavior."""

    def test_selection_fields_rejects_unknown_selection(
        self,
    ) -> None:
        """Unknown task selections fail instead of producing incomplete SQL."""
        with pytest.raises(AssertionError):
            selection_fields(cast("TaskSelection", object()))

    @pytest.mark.parametrize(
        "case",
        [
            {"json_columns": [], "expected": "*"},
            {
                "json_columns": ["units", "data"],
                "expected": '* REPLACE (to_json("units") AS "units", to_json("data") AS "data")',
            },
        ],
        ids=lambda case: "flat" if not case["json_columns"] else "json",
    )
    def test_build_columns(self, case: dict[str, object]) -> None:
        """STRUCT columns are replaced with JSON expressions; flat tables keep `*`."""
        assert (
            build_columns(cast("list[str]", case["json_columns"])).as_string(None)
            == case["expected"]
        )

    @pytest.mark.parametrize(
        "case",
        [
            {
                "name": "all",
                "selection": AllSelection(),
                "path": "duckdb/write_all",
                "field": "gcs_path",
                "expected": "'s3://b/t/data.parquet'",
            },
            {
                "name": "time",
                "selection": TimeRangeSelection(
                    column="dt", lower="2025-01-15", upper="2025-01-16"
                ),
                "path": "duckdb/write_partition",
                "field": "upper",
                "expected": "'2025-01-16'",
            },
            {
                "name": "range",
                "selection": RangeSelection(
                    partition_id="10", column="cpf", lower=10, upper=20
                ),
                "path": "duckdb/write_partition",
                "field": "upper",
                "expected": "20",
            },
            {
                "name": "remainder",
                "selection": RemainderSelection(column="cpf", start=0, end=100),
                "path": "duckdb/write_remainder",
                "field": "upper",
                "expected": "100",
            },
        ],
        ids=lambda case: case["name"],
    )
    def test_build_mapping_selects_template(self, case: dict[str, object]) -> None:
        """A task's discriminated selection chooses its extraction template."""
        task = DumpTask(
            run_id="s1",
            table="p.d.t",
            bucket_path="s3://b/t/data.parquet",
            selection=cast("TaskSelection", case["selection"]),
        )

        spec = build_mapping(task)

        assert spec.path == case["path"]
        assert render(spec.mapping[cast(str, case["field"])]) == case["expected"]

    def test_extract_task_executes_rendered_sql(
        self, duckdb: DuckDBPyConnection
    ) -> None:
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

    def test_build_mapping_rejects_invalid_selection(
        self,
    ) -> None:
        # The exhaustive branch is defensive; use an invalid runtime value.
        """Verify build mapping rejects invalid selection."""
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
