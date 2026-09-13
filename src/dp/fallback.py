"""BigQuery fallback view generation and cache invalidation."""

from dataclasses import dataclass
from typing import LiteralString, cast

from psycopg import AsyncConnection
from psycopg.sql import SQL, Composable, Identifier, Literal

from .conditions import schema_scope_condition, unit_access_condition
from .executor import execute_sql
from .models import SyncConfig, TableConfig, UnitMapping
from .settings import settings
from .templates import render_fragment

DUCKDB_VIEW_PREFIX = "bq_fallback_"


@dataclass(frozen=True, slots=True)
class Column:
    """Pre-rendered SQL fragments for one set of BigQuery columns."""

    bq_select: str
    pg_select: str
    return_types: str
    duckdb_cols: str

    @classmethod
    def from_columns(cls, columns: list[tuple[str, str]]) -> Column:
        """Render all SQL column fragments from BigQuery column metadata."""
        return cls(
            bq_select=", ".join(bigquery_column_expr(c, t) for c, t in columns),
            pg_select=", ".join(postgres_column_cast(c, t) for c, t in columns),
            return_types=", ".join(return_type_for(c, t) for c, t in columns),
            duckdb_cols=", ".join(quoted_identifier(c) for c, _ in columns),
        )


@dataclass(frozen=True, slots=True)
class RLS:
    """Pre-rendered SQL fragments for RLS unit mappings."""

    enabled: str
    unit_values: Composable

    @classmethod
    def from_mappings(cls, mappings: list[UnitMapping] | None) -> RLS:
        """Render RLS SQL fragments from unit mappings, with empty defaults."""
        if not mappings:
            return cls(enabled="false", unit_values=SQL("('','')"))

        return cls(
            enabled="true",
            unit_values=SQL(", ").join(
                SQL("({},{})").format(Literal(m.column), Literal(m.unit_type))
                for m in mappings
            ),
        )


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


def duckdb_type_for(pg_type: str) -> LiteralString:
    """Return the DuckDB read_parquet type for one PostgreSQL column type.

    PostgreSQL jsonb is read as DuckDB json, because pgduckdb cannot cast a
    Parquet jsonb column directly.
    """
    return cast("LiteralString", "json" if pg_type == "jsonb" else pg_type)


def quoted_identifier(identifier: str) -> str:
    """Quote an identifier for PostgreSQL and DuckDB SQL."""
    return Identifier(identifier).as_string(None)


def bigquery_column_expr(column: str, duckdb_type: str) -> str:
    """
    Return the DuckDB SELECT expression for one column.

    STRUCT and ARRAY types are wrapped with to_json() for PostgreSQL jsonb casting.
    """
    quoted = quoted_identifier(column)

    if is_nested_or_json(duckdb_type):
        return f"to_json({quoted}) AS {quoted}"

    return quoted


def postgres_column_cast(column: str, duckdb_type: str) -> str:
    """Return the PostgreSQL cast expression for one duckdb.query() column."""
    quoted = quoted_identifier(column)
    key = Literal(column).as_string(None)

    if is_nested_or_json(duckdb_type):
        return f"r[{key}]::text AS {quoted}"

    return f"r[{key}]::{pg_scalar_type(duckdb_type)} AS {quoted}"


def rls_where_clause(schema: str, table: TableConfig) -> str | Composable:
    """Build the BigQuery WHERE clause from the table's RLS unit mappings.

    Returns an empty string when the table has no RLS.
    """
    if table.rls is None:
        return ""

    claim = settings.sync_config.schemas[schema].claim

    if claim is None:
        return ""

    return render_fragment(
        "postgres/rls_where_clause",
        {
            "schema": Identifier(schema),
            "session_var": Literal(f"app.claim_{claim}"),
            "predicate": unit_access_condition(table.rls),
        },
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


def return_type_for(column: str, duckdb_type: str) -> str:
    """Return the PostgreSQL column type declaration for a RETURNS TABLE clause."""
    if is_nested_or_json(duckdb_type):
        return f"{quoted_identifier(column)} text"

    match duckdb_type.upper():
        case "DATE" | "BOOLEAN":
            return f"{quoted_identifier(column)} {duckdb_type.upper().lower()}"
        case t if t.startswith(("INTEGER", "BIGINT", "INT")):
            return f"{quoted_identifier(column)} bigint"
        case _:
            return f"{quoted_identifier(column)} text"


def bq_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> dict[str, str | Composable]:
    """Return mappings for the CREATE OR REPLACE FUNCTION template."""
    table_name = table.table_name
    fn_name = f"{table_name}_bq_fn"
    duckdb_view = f"{DUCKDB_VIEW_PREFIX}{schema}_{table_name}"
    claim = settings.sync_config.schemas[schema].claim or "sub"

    cols = Column.from_columns(columns)
    rls = RLS.from_mappings(table.rls)

    return {
        "schema": Identifier(schema),
        "fn_name": Identifier(fn_name),
        "return_types": cols.return_types,
        "claim_var": f"app.claim_{claim}",
        "scope": schema_scope_condition(schema),
        "duckdb_view": duckdb_view,
        "bq_select_cols": cols.bq_select,
        "bq_table": table.name,
        "pg_select_cols": cols.pg_select,
        "duckdb_cols": cols.duckdb_cols,
        "has_rls": rls.enabled,
        "unit_values": rls.unit_values,
    }


def bq_view_mapping(
    schema: str, table: TableConfig, columns: list[tuple[str, str]]
) -> dict[str, str | Composable]:
    """Return mappings for the PostgreSQL boundary view template."""
    select_cols = ", ".join(
        f"{quoted_identifier(column)}::jsonb AS {quoted_identifier(column)}"
        if is_nested_or_json(duckdb_type)
        else quoted_identifier(column)
        for column, duckdb_type in columns
    )

    return {
        "schema": Identifier(schema),
        "view_name": Identifier(f"{table.table_name}_bq"),
        "fn_name": Identifier(f"{table.table_name}_bq_fn"),
        "select_cols": select_cols,
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
