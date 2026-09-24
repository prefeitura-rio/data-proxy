"""BigQuery fallback view generation and cache invalidation."""

from dataclasses import dataclass, field
from typing import cast

from psycopg import AsyncConnection
from psycopg.sql import Identifier, Literal

from .catalog import CatalogPaths
from .conditions import schema_scope_condition
from .executor import execute_sql
from .models import SyncConfig, TableConfig
from .settings import settings
from .types import DatabaseRow, TemplateValue

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
    """Return the DuckDB read_parquet type for one PostgreSQL column type."""
    return "json" if pg_type == "jsonb" else pg_type


def quoted_identifier(identifier: str) -> str:
    """Quote an identifier for PostgreSQL and DuckDB SQL."""
    return Identifier(identifier).as_string(None)


async def column_types_from_duckdb(
    pg_conn: AsyncConnection, table: TableConfig
) -> list[tuple[str, str]]:
    """Return (column_name, DuckDB type) pairs from the source table."""
    cursor = await execute_sql(
        pg_conn,
        "postgres/describe_bq_table",
        mapping={"bq_table": Literal(table.name)},
    )

    rows = cast("list[DatabaseRow]", await cursor.fetchall())
    return [(str(row[0]), str(row[1])) for row in rows]


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
        "source": f"bigquery_scan(''{table.name}'')",
        "source_prefix": "LOAD bigquery; ",
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


def ducklake_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Return mappings for the DuckLake SECURITY DEFINER function."""
    table_name = table.table_name
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
    claim = settings.sync_config.schemas[schema].claim or "sub"
    return {
        "schema": Identifier(schema),
        "function": Identifier(f"{table_name}_fn"),
        "columns": column_context,
        "claim_setting": f"app.claim_{claim}",
        "scope": schema_scope_condition(schema),
        "duckdb_view": f"ducklake_{schema}_{table_name}",
        "source": f"dl.{quoted_identifier(table_name)}",
        "catalog_local_path": str(CatalogPaths.for_schema(schema).local),
        "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        "has_rls": "true" if table.rls else "false",
        "rls_mappings": [
            {"column": str(mapping.column), "unit_type": str(mapping.unit_type)}
            for mapping in (table.rls or [])
        ],
    }


def ducklake_view_mapping(
    schema: str, table: TableConfig, columns: list[tuple[str, str]]
) -> dict[str, TemplateValue]:
    """Return mappings for the default DuckLake boundary view."""
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
        "view": Identifier(table.table_name),
        "function": Identifier(f"{table.table_name}_fn"),
        "columns": columns_sql,
    }


@dataclass
class FallbackView:
    """Pipeline state for one fallback view creation."""

    pg_conn: AsyncConnection
    schema: str
    table: TableConfig
    columns: list[tuple[str, str]] = field(default_factory=list)

    async def discover(self) -> None:
        """Discover column types from the local PostgreSQL table."""
        self.columns = await column_types_from_duckdb(self.pg_conn, self.table)

        if not self.columns:
            table_name = f"{self.table.resolved_schema}.{self.table.table_name}"
            raise RuntimeError(
                f"DuckDB returned no columns for fallback table {table_name}"
            )

    async def function(self) -> None:
        """Create the DuckLake SECURITY DEFINER function."""
        await execute_sql(
            self.pg_conn,
            "postgres/create_view_function",
            mapping=ducklake_function_mapping(self.schema, self.table, self.columns),
        )

    async def view(self) -> None:
        """Create the PostgreSQL DuckLake boundary view."""
        await execute_sql(
            self.pg_conn,
            "postgres/create_view",
            mapping=ducklake_view_mapping(self.schema, self.table, self.columns),
        )

    async def fallback_function(self) -> None:
        """Create the BigQuery fallback function."""
        await execute_sql(
            self.pg_conn,
            "postgres/create_view_function",
            mapping=bq_function_mapping(self.schema, self.table, self.columns),
        )

    async def fallback_view(self) -> None:
        """Create the PostgreSQL BigQuery fallback view."""
        await execute_sql(
            self.pg_conn,
            "postgres/create_view",
            mapping=bq_view_mapping(self.schema, self.table, self.columns),
        )

    async def grant(self) -> None:
        """Grant select on the fallback view to the user role."""
        await execute_sql(
            self.pg_conn,
            "postgres/grant_select",
            mapping={
                "schema": Identifier(self.schema),
                "name": Identifier(f"{self.table.table_name}_bq"),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
            },
        )


async def run_fallback_views_creation(
    pg_conn: AsyncConnection, config: SyncConfig
) -> bool:
    """Create configured views and return whether the view set changed."""
    desired: set[tuple[str, str]] = set()
    for schema_name, schema_config in config.schemas.items():
        for table in schema_config.tables:
            desired.add((schema_name, table.table_name))
            if table.fallback:
                desired.add((schema_name, f"{table.table_name}_bq"))

    cursor = await pg_conn.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.views
        WHERE table_schema = ANY(%s)
        """,
        (list(config.schemas),),
    )
    existing_rows = cast("list[DatabaseRow]", await cursor.fetchall())
    existing = {(str(schema), str(name)) for schema, name in existing_rows}
    managed_existing = {
        item for item in existing if any(item[0] == schema for schema in config.schemas)
    }
    changed = managed_existing != desired
    removed = managed_existing - desired
    for schema_name, view_name in removed:
        if view_name in {"access_policy", "freshness", "access_log", "state"}:
            continue
        base_name = view_name.removesuffix("_bq")
        await execute_sql(
            pg_conn,
            "postgres/drop_ducklake_objects",
            mapping={
                "schema": Identifier(schema_name),
                "view": Identifier(base_name),
                "function": Identifier(f"{base_name}_fn"),
                "bq_function": Identifier(f"{base_name}_bq_fn"),
            },
        )

    for schema_name, schema_config in config.schemas.items():
        for table in schema_config.tables:
            ctx = FallbackView(pg_conn=pg_conn, schema=schema_name, table=table)
            await ctx.discover()
            await ctx.function()
            await ctx.view()
            await execute_sql(
                pg_conn,
                "postgres/grant_select",
                mapping={
                    "schema": Identifier(schema_name),
                    "name": Identifier(table.table_name),
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                },
            )

            if table.fallback:
                await ctx.fallback_function()
                await ctx.fallback_view()
                await ctx.grant()

    await pg_conn.commit()
    return changed
