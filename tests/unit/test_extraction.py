"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import NamedTuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data_proxy.extraction import (
    extraction_statement,
    selection_fields,
)
from data_proxy.models import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
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
    """SelectionFields behavior tests."""

    @given(case=st.sampled_from(STATEMENT_CASES))
    def test_encodes_selection_bounds_and_column(self, case: StatementCase) -> None:
        """Encode bounds and columns for a selection."""
        fields = selection_fields(case.selection)
        if isinstance(case.selection, AllSelection):
            assert fields == {}
            return
        assert set(fields) == {"column", "lower", "upper"}

    def test_rejects_unknown_selection_type(
        self, invalid_selection: TaskSelection
    ) -> None:
        """Reject an unknown selection type."""
        with pytest.raises(AssertionError):
            selection_fields(invalid_selection)


class TestExtractionStatements:
    """ExtractionStatements behavior tests."""

    @given(case=st.sampled_from(STATEMENT_CASES))
    def test_selects_template_for_each_selection_type(
        self, case: StatementCase
    ) -> None:
        """Select the extraction template for each selection type."""
        task = dump(selections=[case.selection])
        template, mapping = extraction_statement(task, case.selection, "s3://b/t")
        assert template == case.template
        assert render(mapping["path"]) == "'s3://b/t'"

    def test_rejects_unknown_selection_in_statement_builder(
        self, invalid_selection: TaskSelection
    ) -> None:
        """Reject an unknown selection in the statement builder."""
        with pytest.raises(AssertionError):
            extraction_statement(dump(), invalid_selection, "s3://b/t")

    def test_builds_one_output_path_per_selection(self) -> None:
        """Build separate output paths without merging selections."""
        task = dump(
            bucket_path="s3://b/table/batch/data.parquet",
            selections=[AllSelection(), AllSelection()],
        )
        assert task.output_paths == [
            "s3://b/table/batch/data-0.parquet",
            "s3://b/table/batch/data-1.parquet",
        ]
