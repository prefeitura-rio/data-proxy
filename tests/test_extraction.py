"""Tests for BigQuery-to-Parquet extraction operations."""

from pathlib import Path
from typing import NamedTuple

import pytest
from duckdb import DuckDBPyConnection

from dp.extraction import (
    build_columns,
    extract_task,
    extraction_statement,
    merge_statement,
    selection_fields,
)
from dp.models import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from dp.settings import settings
from tests.helpers import dump, render


class StatementCase(NamedTuple):
    """One selection and the extraction template it must choose."""

    name: str
    selection: TaskSelection
    template: str


STATEMENT_CASES = [
    StatementCase("all", AllSelection(), "duckdb/write_all"),
    StatementCase(
        "time",
        TimeRangeSelection(column="dt", lower="2025-01-01", upper="2025-01-02"),
        "duckdb/write_partition",
    ),
    StatementCase(
        "range",
        RangeSelection(partition_id="10", column="cpf", lower=10, upper=20),
        "duckdb/write_partition",
    ),
    StatementCase(
        "remainder",
        RemainderSelection(column="cpf", start=0, end=100),
        "duckdb/write_remainder",
    ),
]


class TestSelectionFields:
    """Selection mapping behavior."""

    @pytest.mark.parametrize("case", STATEMENT_CASES, ids=lambda case: case.name)
    def test_selection_fields_encode_bounds_and_column(
        self, case: StatementCase
    ) -> None:
        """
        GIVEN: one task selection.
        WHEN: selection_fields is called.
        THEN: it returns bound literals and a column for non-all selections.
        """
        fields = selection_fields(case.selection)

        if isinstance(case.selection, AllSelection):
            assert fields == {}
            return

        assert set(fields) == {"column", "lower", "upper"}

    def test_selection_fields_rejects_an_unknown_selection(
        self, invalid_selection: TaskSelection
    ) -> None:
        """
        GIVEN: an unknown task selection type.
        WHEN: selection_fields is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            selection_fields(invalid_selection)


class TestBuildColumns:
    """STRUCT-to-JSON column rewriting."""

    def test_flat_table_uses_star(self) -> None:
        """
        GIVEN: a table without STRUCT columns.
        WHEN: build_columns is called.
        THEN: it returns a plain star expression.
        """
        assert build_columns([]).as_string(None) == "*"

    def test_struct_columns_are_wrapped_with_json(self) -> None:
        """
        GIVEN: STRUCT column names.
        WHEN: build_columns is called.
        THEN: each column is wrapped with to_json under a star replace.
        """
        rendered = build_columns(["units", "data"]).as_string(None)
        assert rendered == (
            '* REPLACE (to_json("units") AS "units", to_json("data") AS "data")'
        )


class TestExtractionStatement:
    """Extraction template selection."""

    @pytest.mark.parametrize("case", STATEMENT_CASES, ids=lambda case: case.name)
    def test_extraction_statement_selects_a_template(self, case: StatementCase) -> None:
        """
        GIVEN: a dump task and a selection.
        WHEN: extraction_statement is called.
        THEN: it returns the matching template and the task path.
        """
        task = dump(selections=[case.selection])
        template, mapping = extraction_statement(task, case.selection, "s3://b/t")

        assert template == case.template
        assert render(mapping["path"]) == "'s3://b/t'"

    def test_extraction_statement_rejects_an_unknown_selection(
        self, invalid_selection: TaskSelection
    ) -> None:
        """
        GIVEN: an unknown task selection type.
        WHEN: extraction_statement is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            extraction_statement(dump(), invalid_selection, "s3://b/t")


class TestMergeStatement:
    """Scratch-merge template behavior."""

    def test_merge_statement_globs_the_scratch_directory(self) -> None:
        """
        GIVEN: a scratch directory and a destination path.
        WHEN: merge_statement is called.
        THEN: it returns the merge template with the scratch glob and path.
        """
        template, mapping = merge_statement("/scratch", "s3://b/out")
        assert template == "duckdb/merge_batch"
        assert render(mapping["scratch"]) == "'/scratch/*.parquet'"
        assert render(mapping["path"]) == "'s3://b/out'"


class TestExtractTask:
    """Extraction execution against DuckDB."""

    @pytest.mark.asyncio
    async def test_single_selection_writes_directly(
        self,
        duckdb: DuckDBPyConnection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        GIVEN: a task with one selection.
        WHEN: extract_task is called.
        THEN: it writes the destination once and uses no scratch directory.
        """
        calls: list[tuple[str, str]] = []

        async def record(_: object, template: str, mapping: dict[str, str]) -> None:
            calls.append((template, render(mapping["path"])))

        monkeypatch.setattr("dp.extraction.connect_duckdb", lambda: _connect(duckdb))
        monkeypatch.setattr("dp.extraction.execute_sql", record)
        task = dump(bucket_path="s3://b/one.parquet", selections=[AllSelection()])

        await extract_task(task)

        assert calls == [("duckdb/write_all", "'s3://b/one.parquet'")]

    @pytest.mark.asyncio
    async def test_multiple_selections_merge_from_scratch(
        self,
        duckdb: DuckDBPyConnection,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        GIVEN: a task with two selections and a scratch directory.
        WHEN: extract_task is called.
        THEN: it writes each batch then merges them into one destination.
        """
        calls: list[tuple[str, str]] = []
        scratch_files: list[Path] = []

        async def record(_: object, template: str, mapping: dict[str, str]) -> None:
            path = render(mapping["path"]).strip("'")
            calls.append((template, path))
            if template != "duckdb/merge_batch":
                Path(path).write_text("parquet")
                scratch_files.append(Path(path))

        monkeypatch.setattr("dp.extraction.connect_duckdb", lambda: _connect(duckdb))
        monkeypatch.setattr("dp.extraction.execute_sql", record)
        monkeypatch.setattr(settings, "DUMPER_SCRATCH_DIR", tmp_path)
        task = dump(
            bucket_path="s3://b/out.parquet",
            selections=[
                RangeSelection(partition_id="1", column="id", lower=1, upper=2),
                RangeSelection(partition_id="2", column="id", lower=2, upper=3),
            ],
        )

        await extract_task(task)

        assert [template for template, _ in calls] == [
            "duckdb/write_partition",
            "duckdb/write_partition",
            "duckdb/merge_batch",
        ]
        assert calls[-1][1].endswith("out.parquet")
        assert scratch_files
        assert not scratch_files[0].parent.exists()


async def _connect(duckdb: DuckDBPyConnection) -> DuckDBPyConnection:
    """Return the shared DuckDB connection for extraction tests."""
    return duckdb
