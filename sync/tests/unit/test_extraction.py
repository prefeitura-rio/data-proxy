import asyncio
from typing import Final
from unittest.mock import AsyncMock

import pytest

from sync.duckdb import DuckDB
from sync.extraction import build_extraction_query, run_extraction
from sync.sources.partitions import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from sync.templates import to_sql
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
        connection.execute.side_effect = [None, None, RuntimeError("write failed")]
        task = dump_task(selections=[AllSelection(), AllSelection()])

        with pytest.raises(RuntimeError, match="write failed"):
            await run_extraction(connection, task)

        assert connection.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_first_requests_of_concurrent_tasks_never_overlap(self) -> None:
        """The BigQuery extension fails with CURL error 77 when connections of one process send their first request at once."""
        running = 0
        most = 0

        async def execute(sql: str, *, params: object = None) -> None:
            nonlocal running, most
            if "DESCRIBE" not in sql:
                await asyncio.sleep(0.01)
                return
            running += 1
            most = max(most, running)
            await asyncio.sleep(0.05)
            running -= 1

        connections: list[AsyncMock] = []
        for _ in range(4):
            connection = AsyncMock(spec=DuckDB)
            connection.execute.side_effect = execute
            connections.append(connection)

        await asyncio.gather(
            *(run_extraction(connection, dump_task()) for connection in connections)
        )

        assert most == 1
