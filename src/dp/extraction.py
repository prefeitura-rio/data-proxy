"""BigQuery-to-Parquet extraction operations."""

from tempfile import TemporaryDirectory
from typing import assert_never

from psycopg.sql import SQL, Composable, Identifier, Literal

from .duckdb import connect_duckdb
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

type StatementMapping = tuple[str, dict[str, str | Composable]]


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


def build_columns(json_columns: list[str]) -> Composable:
    """Return a SELECT expression that converts STRUCT columns to JSON."""
    if not json_columns:
        return SQL("*")

    replacements = SQL(", ").join(
        SQL("to_json({0}) AS {0}").format(Identifier(col)) for col in json_columns
    )

    return SQL("* REPLACE ({replacements})").format(replacements=replacements)


def extraction_statement(
    task: DumpTask, selection: TaskSelection, path: str
) -> StatementMapping:
    """Return one extraction template and its values."""
    mapping: dict[str, str | Composable] = {
        "bq_table": Literal(task.table),
        "path": Literal(path),
        "columns": build_columns(task.json_columns),
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


def merge_statement(scratch: str, path: str) -> StatementMapping:
    """Return the merge template and its values."""
    return "duckdb/merge_batch", {
        "scratch": Literal(f"{scratch}/*.parquet"),
        "path": Literal(path),
    }


async def extract_task(task: DumpTask) -> None:
    """Write one extraction task to Parquet."""
    db = await connect_duckdb()

    try:
        if len(task.selections) == 1:
            template, mapping = extraction_statement(
                task, task.selections[0], task.bucket_path
            )
            await execute_sql(db, template, mapping)
            return

        with TemporaryDirectory(dir=settings.DUMPER_SCRATCH_DIR) as scratch:
            for index, selection in enumerate(task.selections):
                template, mapping = extraction_statement(
                    task, selection, f"{scratch}/{index}.parquet"
                )
                await execute_sql(db, template, mapping)

            template, mapping = merge_statement(scratch, task.bucket_path)
            await execute_sql(db, template, mapping)
    finally:
        db.close()
