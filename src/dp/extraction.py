"""BigQuery-to-Parquet extraction statement builders and runner."""

from tempfile import TemporaryDirectory
from typing import assert_never

from psycopg.sql import Composable, Identifier, Literal

from .duckdb import DuckDB
from .executor import execute_sql
from .models import (
    AllSelection,
    DumpTask,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from .settings import settings
from .types import TemplateValue

type StatementMapping = tuple[str, dict[str, TemplateValue]]


def selection_fields(selection: TaskSelection) -> dict[str, str | Composable]:
    """Return the column and bound literals encoded by one task selection."""
    match selection:
        case AllSelection():
            return {}
        case (
            RangeSelection(column=column, lower=lower, upper=upper)
            | TimeRangeSelection(column=column, lower=lower, upper=upper)
        ):
            return {
                "column": Identifier(column),
                "lower": Literal(lower),
                "upper": Literal(upper),
            }
        case RemainderSelection(column=column, start=start, end=end):
            return {
                "column": Identifier(column),
                "lower": Literal(start),
                "upper": Literal(end),
            }
        case _:
            assert_never(selection)


def extraction_statement(
    task: DumpTask, selection: TaskSelection, path: str
) -> StatementMapping:
    """Return one extraction template and its values."""
    mapping: dict[str, TemplateValue] = {
        "bq_table": Literal(task.table),
        "path": Literal(path),
        "json_columns": [
            Identifier(column).as_string(None) for column in task.json_columns
        ],
    }

    mapping |= selection_fields(selection)

    match selection:
        case AllSelection():
            return "duckdb/write_all", mapping
        case RangeSelection() | TimeRangeSelection():
            return "duckdb/write_partition", mapping
        case RemainderSelection():
            return "duckdb/write_remainder", mapping
        case _:  # pragma: no cover
            assert_never(selection)


def merge_statement(scratch_path: str, path: str) -> StatementMapping:
    """Return the merge template and its values."""
    return "duckdb/merge_batch", {
        "scratch_path": Literal(f"{scratch_path}/*.parquet"),
        "path": Literal(path),
    }


async def run_extraction(task: DumpTask, duckdb: DuckDB) -> None:
    """Extract one dump task from BigQuery to Parquet via DuckDB."""
    if len(task.selections) == 1:
        template, mapping = extraction_statement(
            task, task.selections[0], task.bucket_path
        )
        await execute_sql(duckdb, template, mapping)
        return

    with TemporaryDirectory(dir=settings.DUMPER_SCRATCH_DIR) as scratch:
        for index, selection in enumerate(task.selections):
            template, mapping = extraction_statement(
                task, selection, f"{scratch}/{index}.parquet"
            )
            await execute_sql(duckdb, template, mapping)

        template, mapping = merge_statement(scratch, task.bucket_path)
        await execute_sql(duckdb, template, mapping)
