"""Source-to-Parquet extraction statement builders and runner."""

import threading
from time import monotonic
from typing import TYPE_CHECKING, assert_never

from asyncer import asyncify
from psycopg.sql import Identifier, Literal

from .executor import Executor
from .sources.partitions import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from .sources.registry import sources

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .duckdb import DuckDB
    from .models import DumpTask
    from .types import DatabaseRow, DuckDBParams, TemplateValue


type Report = Callable[..., Awaitable[None]]
"""Say what an extraction did: the dump workflow passes its log function, which takes a message and fields."""


def build_extraction_query(
    task: DumpTask, selection: TaskSelection, path: str
) -> tuple[str, dict[str, TemplateValue]]:
    """Return one extraction template and its values."""
    mapping: dict[str, TemplateValue] = {
        "source": sources.configure(task.source, task.source_settings).scan(task.table),
        "path": Literal(path),
        "json_columns": [
            Identifier(column).as_string(None) for column in task.json_columns
        ],
    }

    match selection:
        case AllSelection():
            return "duckdb/write_all", mapping
        case (
            RangeSelection(column=column, lower=lower, upper=upper)
            | TimeRangeSelection(column=column, lower=lower, upper=upper)
        ):
            mapping |= {
                "column": Identifier(column),
                "lower": Literal(lower),
                "upper": Literal(upper),
            }
            return "duckdb/write_partition", mapping
        case RemainderSelection(column=column, start=start, end=end):
            mapping |= {
                "column": Identifier(column),
                "lower": Literal(start),
                "upper": Literal(end),
            }
            return "duckdb/write_remainder", mapping
        case _:  # pragma: no cover
            assert_never(selection)


# The BigQuery extension fails with CURL error 77 (a problem with the CA certificates) when several connections of one
# process send their first request at the same time. A connection that has made one request works.
FIRST_REQUEST = threading.Lock()


# DESCRIBE returns the name, the type, null, key, default and extra of each column. A COPY with RETURN_STATS returns
# the file name, the rows, the file size, the footer size, the column statistics and the partition keys of each file.
async def run_extraction(duckdb_conn: DuckDB, task: DumpTask, report: Report) -> None:
    """Extract each selection to its own scratch Parquet file, and report each step."""
    executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb_conn)
    scan = sources.configure(task.source, task.source_settings).scan(task.table)
    await asyncify(FIRST_REQUEST.acquire)()
    try:
        columns = await executor.query(
            "duckdb/describe_source",
            {"source": scan},
            expect=tuple[str, str, object, object, object, object],
        )
    finally:
        FIRST_REQUEST.release()

    await report("Source described", columns=len(columns))

    total = len(task.selections)
    for index, (selection, path) in enumerate(
        zip(task.selections, task.output_paths, strict=True), start=1
    ):
        template, mapping = build_extraction_query(task, selection, path)
        started = monotonic()
        written = await executor.query(
            template,
            mapping,
            expect=tuple[str, int, int, int, object, object],
        )
        await report(
            "Partition written",
            path=path,
            part=index,
            of=total,
            rows=sum(rows for _, rows, _, _, _, _ in written),
            bytes=sum(size for _, _, size, _, _, _ in written),
            seconds=round(monotonic() - started, 1),
        )
