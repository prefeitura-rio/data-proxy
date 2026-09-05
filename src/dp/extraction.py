"""BigQuery-to-Parquet extraction operations."""

from typing import assert_never

from duckdb import DuckDBPyConnection
from psycopg.sql import SQL, Composable, Identifier, Literal

from .models import (
    AllSelection,
    DumpTask,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from .templates import render_template


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


def build_mapping(task: DumpTask) -> str:
    """Return the DuckDB SQL for one extraction task."""
    mapping: dict[str, str | Composable] = {
        "bq_table": Literal(task.table),
        "gcs_path": Literal(task.bucket_path),
        "columns": build_columns(task.json_columns),
    }

    mapping |= selection_fields(task.selection)

    match task.selection:
        case AllSelection():
            return render_template("duckdb/write_all", mapping)
        case RangeSelection() | TimeRangeSelection():
            return render_template("duckdb/write_partition", mapping)
        case RemainderSelection():
            return render_template("duckdb/write_remainder", mapping)
        case _:  # pragma: no cover
            assert_never(task.selection)


def extract_task(task: DumpTask, db: DuckDBPyConnection) -> None:
    """Write one BigQuery task to GCS Parquet through DuckDB."""
    db.execute(build_mapping(task))
