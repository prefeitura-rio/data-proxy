"""BigQuery-to-Parquet extraction statement builders and runner."""

from typing import assert_never

from psycopg.sql import Identifier, Literal

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
from .types import TemplateValue


def build_extraction_query(
    task: DumpTask, selection: TaskSelection, path: str
) -> tuple[str, dict[str, TemplateValue]]:
    """Return one extraction template and its values."""
    mapping: dict[str, TemplateValue] = {
        "bq_table": Literal(task.table),
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


async def run_extraction(task: DumpTask, duckdb_conn: DuckDB) -> None:
    """Extract each selection to its own scratch Parquet file."""
    for selection, path in zip(task.selections, task.output_paths, strict=True):
        template, mapping = build_extraction_query(task, selection, path)
        await execute_sql(duckdb_conn, template, mapping)
