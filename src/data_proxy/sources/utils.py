"""Column and mapping helpers for source view generation."""

from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from ..catalog import CatalogPaths
from ..constants import DUCKDB_VIEW_PREFIX
from ..executor import Executor
from ..models import TableConfig
from ..postgres import Postgres
from ..schema import schema as app_schema
from ..settings import settings
from ..types import PostgresParams, TemplateValue
from .sources import Source, sources


def is_nested_or_json(duckdb_type: str) -> bool:
    """Return true when a DuckDB type carries JSON, alone or nested."""
    upper = duckdb_type.upper()
    return upper.startswith(("STRUCT", "ARRAY", "LIST", "JSON"))


def pg_scalar_type(duckdb_type: str) -> str:
    """Return the PostgreSQL scalar type for one DuckDB column type."""
    match duckdb_type.upper():
        case "DATE" | "BOOLEAN" as t:
            return t.lower()
        case t if t.startswith(("INTEGER", "BIGINT", "INT")):
            return "bigint"
        case _:
            return "text"


def quoted_identifier(identifier: str) -> str:
    """Quote an identifier for PostgreSQL and DuckDB SQL."""
    return Identifier(identifier).as_string(None)


def return_type_for(duckdb_type: str) -> str:
    """Return the PostgreSQL type for one DuckDB column."""
    if is_nested_or_json(duckdb_type):
        return "text"
    return pg_scalar_type(duckdb_type)


def function_columns(
    columns: list[tuple[str, str]], *, raw_json: bool
) -> list[dict[str, TemplateValue]]:
    """Build the column metadata used by query-function templates."""
    return [
        {
            "name": Identifier(column).as_string(None),
            "key": Literal(column).as_string(None),
            "is_json": is_nested_or_json(duckdb_type),
            "raw_json": raw_json,
            "pg_type": pg_scalar_type(duckdb_type),
            "return_type": return_type_for(duckdb_type),
        }
        for column, duckdb_type in columns
    ]


async def column_types_from_duckdb(
    pg_conn: Postgres, table: TableConfig
) -> list[tuple[str, str]]:
    """Return column names and DuckDB types for the configured source table."""
    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)
    await executor.execute("postgres/load_bigquery")
    rows = await executor.query(
        "postgres/describe_bq_table",
        mapping={"bq_table": Literal(table.name)},
        expect=tuple[str, str],
    )
    return [(row[0], row[1]) for row in rows]


def ducklake_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Return mappings for the private DuckLake helper template."""
    table_name = table.table_name
    ducklake = sources.get("ducklake")
    return {
        "schema": Identifier(schema),
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_{ducklake.suffix}"),
        "columns": function_columns(columns, raw_json=True),
        "duckdb_view": f"ducklake_{schema}_{table_name}",
        "source": ducklake.scan(quoted_identifier(table_name)),
        "catalog_local_path": str(CatalogPaths.for_schema(schema).local),
        "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
    }


def source_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    adapter: Source,
) -> dict[str, TemplateValue]:
    """Return mappings for one private source helper template."""
    table_name = table.table_name
    return {
        "schema": Identifier(schema),
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_{adapter.suffix}"),
        "columns": function_columns(columns, raw_json=False),
        "duckdb_view": f"{DUCKDB_VIEW_PREFIX}{schema}_{table_name}",
        "load": adapter.load,
        "source": adapter.scan(table.name),
    }


def boundary_view_mapping(
    schema: str,
    view_name: str,
    function_name: str,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Build mappings for a PostgreSQL boundary view."""
    columns_sql = [
        (
            f"{quoted_identifier(column)}::jsonb AS {quoted_identifier(column)}"
            if is_nested_or_json(duckdb_type)
            else quoted_identifier(column)
        )
        for column, duckdb_type in columns
    ]
    return {
        "schema": Identifier(schema),
        "view": Identifier(view_name),
        "function": Identifier(function_name),
        "columns": columns_sql,
    }


def table_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Return mappings for the function behind one table view."""
    table_name = table.table_name
    claim = settings.sync_config.schemas[schema].claim or "sub"
    ducklake = sources.get("ducklake")
    return {
        "schema": Identifier(schema),
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_fn"),
        "dl_function": Identifier(f"{table_name}_{ducklake.suffix}"),
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim}",
        "has_rls": "true" if table.rls else "false",
        "rls_mappings": [
            {"column": str(mapping.column), "unit_type": str(mapping.unit_type)}
            for mapping in (table.rls or [])
        ],
        "source_table": Literal(table.name),
        "fallbacks": [
            {
                "name": name,
                "function": Identifier(f"{table_name}_{sources.get(name).suffix}"),
            }
            for name in table.fallbacks
        ],
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }


def table_changes_function_mapping(
    schema: str, table: TableConfig, columns: list[tuple[str, str]]
) -> dict[str, TemplateValue]:
    """Return mappings for one DuckLake table change-feed function."""
    claim = settings.sync_config.schemas[schema].claim or "sub"
    return {
        "schema": Identifier(schema),
        "function": Identifier(f"ducklake_changes_{table.table_name}"),
        "table_name": table.table_name,
        "duckdb_view": f"ducklake_changes_{schema}_{table.table_name}",
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim}",
        "has_rls": "true" if table.rls else "false",
        "rls_mappings": [
            {"column": str(mapping.column), "unit_type": str(mapping.unit_type)}
            for mapping in (table.rls or [])
        ],
        "catalog_local_path": str(CatalogPaths.for_schema(schema).local),
        "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }
