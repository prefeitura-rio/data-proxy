"""Tests for BigQuery-to-Parquet extraction operations."""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class BuildColumnsCase:
    """One column-building case for parametrized testing."""

    json_columns: list[str]
    expected: str


@dataclass(frozen=True, slots=True)
class BuildMappingCase:
    """One template-selection case for parametrized testing."""

    name: str
    selection: TaskSelection
    path: str
    field: str
    expected: str


class TestExtraction:
    """Tests for extraction selection and SQL behavior."""

    def test_selection_fields_rejects_unknown_selection(
        self,
        invalid_selection: TaskSelection,
    ) -> None:
        """
        GIVEN: an unknown task selection type.
        WHEN: selection_fields is called.
        THEN: it raises AssertionError instead of producing incomplete SQL.
        """
        with pytest.raises(AssertionError):
            selection_fields(invalid_selection)

    @pytest.mark.parametrize(
        "case",
        [
            BuildColumnsCase([], "*"),
            BuildColumnsCase(
                ["units", "data"],
                '* REPLACE (to_json("units") AS "units", to_json("data") AS "data")',
            ),
        ],
        ids=lambda case: "flat" if not case.json_columns else "json",
    )
    def test_build_columns_replaces_struct_with_json_and_keeps_flat_star(
        self, case: BuildColumnsCase
    ) -> None:
        """
        GIVEN: a list of STRUCT column names.
        WHEN: build_columns is called.
        THEN: STRUCT columns are replaced with JSON expressions and flat tables keep `*`.
        """
        assert build_columns(case.json_columns).as_string(None) == case.expected

    @pytest.mark.parametrize(
        "case",
        [
            BuildMappingCase(
                "all",
                AllSelection(),
                "duckdb/write_all",
                "gcs_path",
                "'s3://b/t/data.parquet'",
            ),
            BuildMappingCase(
                "time",
                TimeRangeSelection(column="dt", lower="2025-01-15", upper="2025-01-16"),
                "duckdb/write_partition",
                "upper",
                "'2025-01-16'",
            ),
            BuildMappingCase(
                "range",
                RangeSelection(partition_id="10", column="cpf", lower=10, upper=20),
                "duckdb/write_partition",
                "upper",
                "20",
            ),
            BuildMappingCase(
                "remainder",
                RemainderSelection(column="cpf", start=0, end=100),
                "duckdb/write_remainder",
                "upper",
                "100",
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_build_mapping_selects_correct_template_for_each_selection_type(
        self, case: BuildMappingCase
    ) -> None:
        """
        GIVEN: a dump task with a discriminated selection.
        WHEN: build_mapping is called.
        THEN: the selection chooses its correct extraction template and field value.
        """
        task = DumpTask(
            run_id="s1",
            table="p.d.t",
            bucket_path="s3://b/t/data.parquet",
            selection=case.selection,
        )

        spec = build_mapping(task)

        assert spec.path == case.path
        assert render(spec.mapping[case.field]) == case.expected

    def test_extract_task_executes_rendered_sql(
        self, duckdb: DuckDBPyConnection, standard_dump_task: DumpTask
    ) -> None:
        """
        GIVEN: a dump task and a DuckDB connection.
        WHEN: extract_task is called.
        THEN: it executes one rendered SQL statement through DuckDB.
        """
        with patch("dp.extraction.load_template", return_value="SELECT 1"):
            extract_task(standard_dump_task, duckdb)

        assert duckdb.execute("SELECT 1").fetchone() == (1,)

    def test_build_mapping_rejects_an_invalid_selection_type(
        self,
        invalid_dump_task: DumpTask,
    ) -> None:
        """
        GIVEN: a dump task with an invalid selection type.
        WHEN: build_mapping is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            build_mapping(invalid_dump_task)
