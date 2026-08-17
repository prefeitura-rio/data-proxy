"""BigQuery-to-Parquet extraction operations."""

from psycopg.sql import SQL, Composable, Identifier, Literal

from .duckdb import connect
from .models import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    SyncTask,
    ValueSelection,
)
from .templates import TemplateSpec, load_template


def build_columns(json_columns: list[str]) -> Composable:
    """Return a SELECT expression that converts STRUCT columns to JSON."""
    if not json_columns:
        return SQL("*")

    replacements = SQL(", ").join(
        SQL("to_json({0}) AS {0}").format(Identifier(col)) for col in json_columns
    )

    return SQL("* REPLACE ({replacements})").format(replacements=replacements)


def build_mapping(task: SyncTask) -> TemplateSpec:
    """Return the DuckDB template and values for one extraction task."""
    mapping: dict[str, str | Composable] = {
        "bq_table": Literal(task.bq_table),
        "gcs_path": Literal(task.gcs_path),
        "columns": build_columns(task.json_columns),
    }

    match task.selection:
        case AllSelection():
            return {"path": "duckdb/write_all", "mapping": mapping}
        case ValueSelection(column=column, value=value):
            mapping["partition_column"] = Identifier(column)
            mapping["partition_value"] = Literal(value)
            return {"path": "duckdb/write_window", "mapping": mapping}
        case RangeSelection(column=column, lower=lower, upper=upper):
            mapping["partition_column"] = Identifier(column)
            mapping["partition_lower"] = Literal(lower)
            mapping["partition_upper"] = Literal(upper)
            return {"path": "duckdb/write_partition", "mapping": mapping}
        case RemainderSelection(column=column, start=start, end=end):
            mapping["partition_column"] = Identifier(column)
            mapping["partition_lower"] = Literal(start)
            mapping["partition_upper"] = Literal(end)
            return {"path": "duckdb/write_remainder", "mapping": mapping}


def extract_task(task: SyncTask) -> None:
    """Write one BigQuery task to GCS Parquet through DuckDB."""
    spec = build_mapping(task)

    with connect() as db:
        db.execute(load_template(spec))
