"""Tests for BigQuery-to-Parquet extraction operations."""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import NamedTuple

import pytest

from dp.duckdb import DuckDB
from dp.extraction import (
    extraction_statement,
    merge_statement,
    selection_fields,
)
from dp.main import extract
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
        assert render(mapping["scratch_path"]) == "'/scratch/*.parquet'"
        assert render(mapping["path"]) == "'s3://b/out'"


class TestExtract:
    """Extraction execution against DuckDB."""

    @pytest.mark.asyncio
    async def test_single_selection_writes_directly(
        self,
        duckdb: DuckDB,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        GIVEN: a task with one selection.
        WHEN: extract is called.
        THEN: it writes the destination once and uses no scratch directory.
        """
        calls: list[tuple[str, str]] = []

        def record_render(path: str, mapping: dict[str, object]) -> str:
            calls.append((path, render(mapping["path"])))
            return "SELECT 1"

        monkeypatch.setattr(DuckDB, "connect", _connect(duckdb))
        monkeypatch.setattr("dp.executor.render_template", record_render)
        task = dump(bucket_path="s3://b/one.parquet", selections=[AllSelection()])

        await extract(task)

        assert calls == [("duckdb/write_all", "'s3://b/one.parquet'")]

    @pytest.mark.asyncio
    async def test_multiple_selections_merge_from_scratch(
        self,
        duckdb: DuckDB,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        GIVEN: a task with two selections and a scratch directory.
        WHEN: extract is called.
        THEN: it writes each batch then merges them into one destination.
        """
        calls: list[tuple[str, str]] = []
        scratch_files: list[Path] = []

        def record_render(path: str, mapping: dict[str, object]) -> str:
            target = render(mapping["path"]).strip("'")
            calls.append((path, target))
            if path != "duckdb/merge_batch":
                Path(target).write_text("parquet")
                scratch_files.append(Path(target))
            return "SELECT 1"

        monkeypatch.setattr(DuckDB, "connect", _connect(duckdb))
        monkeypatch.setattr("dp.executor.render_template", record_render)
        monkeypatch.setattr(settings, "DUMPER_SCRATCH_DIR", tmp_path)
        task = dump(
            bucket_path="s3://b/out.parquet",
            selections=[
                RangeSelection(partition_id="1", column="id", lower=1, upper=2),
                RangeSelection(partition_id="2", column="id", lower=2, upper=3),
            ],
        )

        await extract(task)

        assert [template for template, _ in calls] == [
            "duckdb/write_partition",
            "duckdb/write_partition",
            "duckdb/merge_batch",
        ]
        assert calls[-1][1].endswith("out.parquet")
        assert scratch_files
        assert not scratch_files[0].parent.exists()


def _connect(facade: DuckDB) -> Callable[[], AbstractAsyncContextManager[DuckDB]]:
    """Return a DuckDB.connect stand-in that yields a prepared facade."""

    @asynccontextmanager
    async def connect() -> AsyncGenerator[DuckDB]:
        yield facade

    return connect
