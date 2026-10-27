import asyncio
from pathlib import Path
from typing import Final
from unittest.mock import AsyncMock

import duckdb
import pytest
from psycopg.sql import SQL, Composable

from sync.duckdb import DuckDB
from sync.extraction import build_extraction_query, run_extraction
from sync.sources.partitions import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from sync.sources.registry import sources
from sync.templates import to_sql
from tests.helpers import dump_task

BASE_MAPPING: Final[dict[str, str | list[str]]] = {
    "source": "bigquery_scan('p.d.t')",
    "path": "'s3://b/t'",
    "json_columns": [],
}


COPY_ROW: Final = ("s3://b/t", 10, 2048, 100, None, None)
"""What DuckDB returns for a COPY with RETURN_STATS."""


async def silent(message: str, /, **fields: str | float | bool) -> None:
    """Ignore a report."""


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

    @pytest.mark.asyncio
    async def test_propagates_failure_from_one_selection(self) -> None:
        """Stop extraction when one output cannot be written."""
        connection = AsyncMock(spec=DuckDB)
        no_columns: list[tuple[object, ...]] = []
        connection.query.side_effect = [
            no_columns,
            [COPY_ROW],
            RuntimeError("write failed"),
        ]
        task = dump_task(selections=[AllSelection(), AllSelection()])

        with pytest.raises(RuntimeError, match="write failed"):
            await run_extraction(connection, task, silent)

        assert connection.query.await_count == 3

    @pytest.mark.asyncio
    async def test_first_requests_of_concurrent_tasks_never_overlap(self) -> None:
        """The BigQuery extension fails with CURL error 77 when connections of one process send their first request at once."""
        running = 0
        most = 0

        async def query(sql: str, *, params: object = None) -> list[tuple[object, ...]]:
            nonlocal running, most
            if "DESCRIBE" not in sql:
                await asyncio.sleep(0.01)
                return [COPY_ROW]
            running += 1
            most = max(most, running)
            await asyncio.sleep(0.05)
            running -= 1
            return []

        connections: list[AsyncMock] = []
        for _ in range(4):
            connection = AsyncMock(spec=DuckDB)
            connection.query.side_effect = query
            connections.append(connection)

        await asyncio.gather(
            *(
                run_extraction(connection, dump_task(), silent)
                for connection in connections
            )
        )

        assert most == 1


class TestExtractionReports:
    """What an extraction tells about its work."""

    @pytest.mark.asyncio
    async def test_reports_the_rows_and_the_size_of_each_partition(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each partition gets a line with its rows and the size of its file, in the order of the selections."""

        class Twenty:
            def scan(self, table: str) -> Composable:
                return SQL("(SELECT i AS cpf FROM range(20) AS t(i))")

        def configure(name: str, settings: object) -> Twenty:
            return Twenty()

        monkeypatch.setattr(sources, "configure", configure)
        reports: list[tuple[str, dict[str, str | int | float | bool]]] = []

        async def report(message: str, /, **fields: str | float | bool) -> None:
            reports.append((message, fields))

        task = dump_task(
            bucket_path=str(tmp_path / "t.parquet"),
            selections=[
                RangeSelection(partition_id="0", column="cpf", lower=0, upper=5),
                RangeSelection(partition_id="1", column="cpf", lower=5, upper=20),
            ],
        )
        connection = duckdb.connect()

        await run_extraction(DuckDB(connection=connection), task, report)

        connection.close()
        assert [(message, fields.get("rows")) for message, fields in reports] == [
            ("Source described", None),
            ("Partition written", 5),
            ("Partition written", 15),
        ]
        assert reports[0][1] == {"columns": 1}
        assert [fields["part"] for _, fields in reports[1:]] == [1, 2]
        assert all(fields["of"] == 2 for _, fields in reports[1:])
        for _, fields in reports[1:]:
            written = await asyncio.to_thread(Path(str(fields["path"])).stat)

            assert fields["bytes"] == written.st_size
