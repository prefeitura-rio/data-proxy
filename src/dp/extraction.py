"""BigQuery-to-Parquet extraction operations."""

from psycopg import sql

from .duckdb import connect
from .models import SyncTask
from .templates import TemplateSpec, load_template


def build_columns(json_columns: list[str]) -> sql.Composable:
    """Return a SELECT expression that converts STRUCT columns to JSON."""
    if not json_columns:
        return sql.SQL("*")

    replacements = sql.SQL(", ").join(
        sql.SQL("to_json({0}) AS {0}").format(sql.Identifier(col))
        for col in json_columns
    )
    return sql.SQL("* REPLACE ({replacements})").format(replacements=replacements)


def build_mapping(task: SyncTask) -> TemplateSpec:
    """Return the DuckDB template and values for one extraction task."""
    mapping: dict[str, str | sql.Composable] = {
        "bq_table": sql.Literal(task.bq_table),
        "gcs_path": sql.Literal(task.gcs_path),
        "columns": build_columns(task.json_columns),
    }

    if task.partition_column and task.partition_value:
        mapping["partition_column"] = sql.Identifier(task.partition_column)
        mapping["partition_value"] = sql.Literal(task.partition_value)
        return {"path": "duckdb/write_window", "mapping": mapping}

    return {"path": "duckdb/write_dump", "mapping": mapping}


def extract_task(task: SyncTask) -> None:
    """Write one BigQuery task to GCS Parquet through DuckDB."""
    spec = build_mapping(task)

    with connect() as db:
        db.execute(load_template(spec))
