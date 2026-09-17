"""BigQuery fallback view generation and cache invalidation."""

from typing import cast

from psycopg import AsyncConnection
from psycopg.sql import SQL, Composable, Identifier, Literal

from .conditions import schema_scope_condition
from .executor import execute_sql
from .models import SyncConfig, TableConfig
from .settings import settings
from .templates import render_template
from .types import TemplateValue

DUCKDB_VIEW_PREFIX = "bq_fallback_"


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


def duckdb_type_for(pg_type: str) -> str:
    """Return the DuckDB read_parquet type for one PostgreSQL column type.

    PostgreSQL jsonb is read as DuckDB json, because pgduckdb cannot cast a
    Parquet jsonb column directly.
    """
    return "json" if pg_type == "jsonb" else pg_type


def quoted_identifier(identifier: str) -> str:
    """Quote an identifier for PostgreSQL and DuckDB SQL."""
    return Identifier(identifier).as_string(None)


def rls_where_clause(schema: str, table: TableConfig) -> str | Composable:
    """Build the BigQuery WHERE clause from the table's RLS unit mappings.

    Returns an empty string when the table has no RLS.
    """
    if table.rls is None:
        return ""

    claim = settings.sync_config.schemas[schema].claim

    if claim is None:
        return ""

    return SQL(
        render_template(
            "postgres/rls_where_clause",
            {
                "schema": Identifier(schema),
                "claim_setting": Literal(f"app.claim_{claim}"),
                "scope": schema_scope_condition(schema),
                "rls_mappings": [
                    {"column": mapping.column, "unit_type": mapping.unit_type}
                    for mapping in table.rls
                ],
            },
        )
    )


async def column_types_from_duckdb(
    conn: AsyncConnection, table: TableConfig
) -> list[tuple[str, str]]:
    """Return (column_name, duckdb_type) pairs from the local PostgreSQL table."""
    cursor = await execute_sql(
        conn,
        "postgres/column_types",
        params=(table.resolved_schema, table.table_name),
    )

    rows = cast("list[tuple[object, object]]", await cursor.fetchall())
    return [(str(column), str(duckdb_type)) for column, duckdb_type in rows]


def return_type_for(duckdb_type: str) -> str:
    """Return the PostgreSQL type for one DuckDB column."""
    if is_nested_or_json(duckdb_type):
        return "text"

    return pg_scalar_type(duckdb_type)


def bq_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Return mappings for the CREATE OR REPLACE FUNCTION template."""
    table_name = table.table_name
    fn_name = f"{table_name}_bq_fn"
    duckdb_view = f"{DUCKDB_VIEW_PREFIX}{schema}_{table_name}"
    claim = settings.sync_config.schemas[schema].claim or "sub"

    column_context = [
        {
            "name": Identifier(column).as_string(None),
            "key": Literal(column).as_string(None),
            "is_json": is_nested_or_json(duckdb_type),
            "pg_type": pg_scalar_type(duckdb_type),
            "return_type": return_type_for(duckdb_type),
        }
        for column, duckdb_type in columns
    ]

    return {
        "schema": Identifier(schema),
        "function": Identifier(fn_name),
        "columns": column_context,
        "claim_setting": f"app.claim_{claim}",
        "scope": schema_scope_condition(schema),
        "duckdb_view": duckdb_view,
        "bq_table": table.name,
        "has_rls": "true" if table.rls else "false",
        "rls_mappings": [
            {"column": str(mapping.column), "unit_type": str(mapping.unit_type)}
            for mapping in (table.rls or [])
        ],
    }


def bq_view_mapping(
    schema: str, table: TableConfig, columns: list[tuple[str, str]]
) -> dict[str, TemplateValue]:
    """Return mappings for the PostgreSQL boundary view template."""
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
        "view": Identifier(f"{table.table_name}_bq"),
        "function": Identifier(f"{table.table_name}_bq_fn"),
        "columns": columns_sql,
    }


async def create_bq_views(conn: AsyncConnection, config: SyncConfig) -> None:
    """Create or replace _bq views for all fallback-enabled tables."""
    for schema_name, schema_config in config.schemas.items():
        for table in schema_config.tables:
            if not table.fallback:
                continue

            columns = await column_types_from_duckdb(conn, table)

            await execute_sql(
                conn,
                "postgres/create_bq_function",
                mapping=bq_function_mapping(schema_name, table, columns),
            )

            await execute_sql(
                conn,
                "postgres/create_bq_view",
                mapping=bq_view_mapping(schema_name, table, columns),
            )

            await execute_sql(
                conn,
                "postgres/grant_bq_select",
                mapping={
                    "schema": Identifier(schema_name),
                    "view": Identifier(f"{table.table_name}_bq"),
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                },
            )

    await conn.commit()
