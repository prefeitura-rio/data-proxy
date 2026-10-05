"""Tests for BigQuery-to-Parquet extraction operations."""

from typing import Final
from unittest.mock import AsyncMock

import pytest

from data_proxy.duckdb import DuckDB
from data_proxy.extraction import build_extraction_query, run_extraction
from data_proxy.sources.partitions import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from data_proxy.templates import to_sql
from tests.helpers import dump_task

BASE_MAPPING: Final[dict[str, str | list[str]]] = {
    "source": "bigquery_scan('p.d.t')",
    "path": "'s3://b/t'",
    "json_columns": [],
}


class TestExtractionStatements:
    """Extraction statement behavior tests."""

    @pytest.mark.parametrize(
        ("selection", "template", "bounds"),
        [
            pytest.param(AllSelection(), "duckdb/write_all", {}, id="all"),
            pytest.param(
                TimeRangeSelection(column="dt", lower="2025-01-01", upper="2025-01-02"),
                "duckdb/write_partition",
                {"column": '"dt"', "lower": "'2025-01-01'", "upper": "'2025-01-02'"},
                id="time",
            ),
            pytest.param(
                RangeSelection(partition_id="10", column="cpf", lower=10, upper=20),
                "duckdb/write_partition",
                {"column": '"cpf"', "lower": "10", "upper": "20"},
                id="range",
            ),
            pytest.param(
                RemainderSelection(column="cpf", start=0, end=100),
                "duckdb/write_remainder",
                {"column": '"cpf"', "lower": "0", "upper": "100"},
                id="remainder",
            ),
        ],
    )
    def test_selects_template_and_encodes_bounds_for_each_selection_type(
        self, selection: TaskSelection, template: str, bounds: dict[str, str]
    ) -> None:
        """Select the extraction template and encode column and bounds."""
        actual_template, mapping = build_extraction_query(
            dump_task(selections=[selection]), selection, "s3://b/t"
        )

        assert actual_template == template
        assert {
            key: to_sql(value) for key, value in mapping.items()
        } == BASE_MAPPING | bounds

    def test_rejects_unknown_selection_in_statement_builder(
        self, invalid_selection: TaskSelection
    ) -> None:
        """Reject an unknown selection in the statement builder."""
        with pytest.raises(AssertionError):
            build_extraction_query(dump_task(), invalid_selection, "s3://b/t")

    @pytest.mark.asyncio
    async def test_propagates_failure_from_one_selection(self) -> None:
        """Stop extraction when one output cannot be written."""
        connection = AsyncMock(spec=DuckDB)
        connection.execute.side_effect = [None, RuntimeError("write failed")]
        task = dump_task(selections=[AllSelection(), AllSelection()])

        with pytest.raises(RuntimeError, match="write failed"):
            await run_extraction(connection, task)

        assert connection.execute.await_count == 2
