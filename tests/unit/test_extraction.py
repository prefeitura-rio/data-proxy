"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import NamedTuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data_proxy.extraction import build_extraction_query
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


class TestExtractionStatements:
    """Extraction statement behavior tests."""

    @given(case=st.sampled_from(STATEMENT_CASES))
    def test_selects_template_and_encodes_bounds_for_each_selection_type(
        self, case: StatementCase
    ) -> None:
        """Select the extraction template and encode column and bounds."""
        task = dump(selections=[case.selection])
        template, mapping = build_extraction_query(task, case.selection, "s3://b/t")
        assert template == case.template
        assert render(mapping["path"]) == "'s3://b/t'"

        if isinstance(case.selection, AllSelection):
            assert "column" not in mapping
            assert "lower" not in mapping
            assert "upper" not in mapping
        else:
            assert {"column", "lower", "upper"}.issubset(mapping.keys())

    def test_rejects_unknown_selection_in_statement_builder(
        self, invalid_selection: TaskSelection
    ) -> None:
        """Reject an unknown selection in the statement builder."""
        with pytest.raises(AssertionError):
            build_extraction_query(dump(), invalid_selection, "s3://b/t")

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
