"""BigQuery-to-Parquet extraction operations."""

from typing import assert_never

from psycopg.sql import SQL, Composable, Identifier, Literal

from .models import (
    AllSelection,
    DumpTask,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from .protocols import DuckDBConnection
from .templates import TemplateSpec, load_template, selection_fields


def build_columns(json_columns: list[str]) -> Composable:
    """Return a SELECT expression that converts STRUCT columns to JSON."""
    if not json_columns:
        return SQL("*")

    replacements = SQL(", ").join(
        SQL("to_json({0}) AS {0}").format(Identifier(col)) for col in json_columns
    )

    return SQL("* REPLACE ({replacements})").format(replacements=replacements)


def build_mapping(task: DumpTask) -> TemplateSpec:
    """Return the DuckDB template and values for one extraction task."""
    mapping: dict[str, str | Composable] = {
        "bq_table": Literal(task.table),
        "gcs_path": Literal(task.bucket_path),
        "columns": build_columns(task.json_columns),
    }

    mapping |= selection_fields(task.selection)

    match task.selection:
        case AllSelection():
            return TemplateSpec(path="duckdb/write_all", mapping=mapping)
        case RangeSelection() | TimeRangeSelection():
            return TemplateSpec(path="duckdb/write_partition", mapping=mapping)
        case RemainderSelection():
            return TemplateSpec(path="duckdb/write_remainder", mapping=mapping)
        case _:
            assert_never(task.selection)


def extract_task(task: DumpTask, db: DuckDBConnection) -> None:
    """Write one BigQuery task to GCS Parquet through DuckDB."""
    spec = build_mapping(task)
    db.execute(load_template(spec))
