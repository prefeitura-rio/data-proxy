"""BigQuery-to-Parquet extraction operations."""

from .duckdb import connect
from .models import SyncTask
from .templates import load_template


def build_columns(json_columns: list[str]) -> str:
    """Return a SELECT expression that converts STRUCT columns to JSON."""
    if not json_columns:
        return "*"

    replacements = ", ".join(f'to_json("{col}") AS "{col}"' for col in json_columns)
    return f"* REPLACE ({replacements})"


def build_mapping(task: SyncTask) -> tuple[str, dict[str, str]]:
    """Return the DuckDB template and values for one extraction task."""
    mapping = {
        "bq_table": task.bq_table,
        "gcs_path": task.gcs_path,
        "columns": build_columns(task.json_columns),
    }

    if task.partition_column and task.partition_value:
        mapping["partition_column"] = task.partition_column
        mapping["partition_value"] = task.partition_value
        return "duckdb/write_window", mapping

    return "duckdb/write_dump", mapping


def extract_task(task: SyncTask) -> None:
    """Write one BigQuery task to GCS Parquet through DuckDB."""
    template, mapping = build_mapping(task)

    with connect() as db:
        db.execute(load_template(template, mapping))
