"""DuckLake and BigQuery fallback view generation."""

from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from .catalog import CatalogPaths
from .conditions import schema_scope_condition
from .constants import DUCKDB_VIEW_PREFIX, PROTECTED_VIEW_NAMES
from .executor import Executor
from .models import SyncConfig, TableConfig
from .postgres import Postgres
from .settings import settings
from .types import PostgresParams, TemplateValue


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
    pg_conn: Postgres, table: TableConfig
) -> list[tuple[str, str]]:
    """Return column names and DuckDB types for the configured BigQuery table."""
    rows = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/describe_bq_table",
        mapping={"bq_table": Literal(table.name)},
        expect=tuple[str, str],
    )

    return [(row[0], row[1]) for row in rows]


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
    column_context = function_columns(columns, raw_json=False)

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


def ducklake_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Return mappings for the DuckLake SECURITY DEFINER function."""
    table_name = table.table_name
    column_context = function_columns(columns, raw_json=True)
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


def changes_function_name(table: TableConfig) -> str:
    """Return the managed change-feed function name for one table."""
    return f"ducklake_changes_{table.table_name}"


def desired_functions(config: SyncConfig) -> set[tuple[str, str]]:
    """Return managed change-feed and snapshot functions."""
    functions = {
        (schema_name, "ducklake_latest_snapshot") for schema_name in config.schemas
    }
    functions.update(
        (schema_name, changes_function_name(table))
        for schema_name, schema_config in config.schemas.items()
        for table in schema_config.tables
    )
    return functions


def desired_views(config: SyncConfig) -> set[tuple[str, str]]:
    """Return configured DuckLake views and enabled BigQuery fallback views."""
    views: set[tuple[str, str]] = set()

    for schema_name, schema_config in config.schemas.items():
        for table in schema_config.tables:
            views.add((schema_name, table.table_name))

            if table.fallback:
                views.add((schema_name, f"{table.table_name}_bq"))

    return views


async def existing_functions(
    pg_conn: Postgres, schema_names: list[str]
) -> set[tuple[str, str]]:
    """Return managed PostgreSQL functions in the configured schemas."""
    rows = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/list_functions",
        mapping={"schemas": Literal(schema_names)},
        expect=tuple[str, str],
    )
    return {
        (schema, name)
        for schema, name in rows
        if name.startswith("ducklake_changes_") or name == "ducklake_latest_snapshot"
    }


async def existing_views(
    pg_conn: Postgres, schema_names: list[str]
) -> set[tuple[str, str]]:
    """Return PostgreSQL views in the configured schemas."""
    rows = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/list_views",
        mapping={"schemas": Literal(schema_names)},
        expect=tuple[str, str],
    )

    return {(schema, name) for schema, name in rows}


async def drop_removed_functions(
    pg_conn: Postgres, removed: set[tuple[str, str]]
) -> None:
    """Drop managed RPC functions no longer present in the configuration."""
    for schema_name, function_name in removed:
        arguments = (
            "(bigint, bigint)"
            if function_name.startswith("ducklake_changes_")
            else "()"
        )
        await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
            "postgres/drop_function",
            mapping={
                "schema": Identifier(schema_name),
                "function": Identifier(function_name),
                "arguments": arguments,
            },
        )


async def drop_removed_views(pg_conn: Postgres, removed: set[tuple[str, str]]) -> None:
    """Drop functions and views for names no longer in the sync config."""
    for schema_name, view_name in removed:
        if view_name in PROTECTED_VIEW_NAMES:
            continue

        base_name = view_name.removesuffix("_bq")

        await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
            "postgres/drop_ducklake_objects",
            mapping={
                "schema": Identifier(schema_name),
                "view": Identifier(base_name),
                "bq_view": Identifier(f"{base_name}_bq"),
                "function": Identifier(f"{base_name}_fn"),
                "bq_function": Identifier(f"{base_name}_bq_fn"),
            },
        )


def table_changes_function_mapping(
    schema: str, table: TableConfig, columns: list[tuple[str, str]]
) -> dict[str, TemplateValue]:
    """Return mappings for one DuckLake table change-feed function."""
    claim = settings.sync_config.schemas[schema].claim or "sub"
    return {
        "schema": Identifier(schema),
        "function": Identifier(changes_function_name(table)),
        "table_name": table.table_name,
        "duckdb_view": f"ducklake_changes_{schema}_{table.table_name}",
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim}",
        "scope": schema_scope_condition(schema),
        "has_rls": "true" if table.rls else "false",
        "rls_mappings": [
            {"column": str(mapping.column), "unit_type": str(mapping.unit_type)}
            for mapping in (table.rls or [])
        ],
        "catalog_local_path": str(CatalogPaths.for_schema(schema).local),
        "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }


async def create_table_changes_function(
    pg_conn: Postgres,
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> None:
    """Create one typed DuckLake change-feed function."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_table_changes_function",
        mapping=table_changes_function_mapping(schema, table, columns),
    )


async def create_current_snapshot_function(pg_conn: Postgres, schema: str) -> None:
    """Create the schema-scoped current snapshot function."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_current_snapshot_function",
        mapping={
            "schema": Identifier(schema),
            "function": Identifier("ducklake_latest_snapshot"),
            "scope": schema_scope_condition(schema),
            "catalog_local_path": str(CatalogPaths.for_schema(schema).local),
            "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
            "user_role": Identifier(settings.AUTH_USER_ROLE),
        },
    )


async def grant_view_select(pg_conn: Postgres, schema: str, view_name: str) -> None:
    """Grant select on a configured view to the user role."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/grant_select",
        mapping={
            "schema": Identifier(schema),
            "object": Identifier(view_name),
            "user_role": Identifier(settings.AUTH_USER_ROLE),
        },
    )


async def create_table_views(
    pg_conn: Postgres, schema: str, table: TableConfig
) -> None:
    """Create DuckLake views and the configured optional BigQuery fallback."""
    columns = await column_types_from_duckdb(pg_conn, table)

    if not columns:
        table_name = f"{table.resolved_schema}.{table.table_name}"
        raise RuntimeError(f"DuckDB returned no columns for table {table_name}")

    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_view_function",
        mapping=ducklake_function_mapping(schema, table, columns),
    )

    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_view",
        mapping=boundary_view_mapping(
            schema, table.table_name, f"{table.table_name}_fn", columns
        ),
    )

    await grant_view_select(pg_conn, schema, table.table_name)
    await create_table_changes_function(pg_conn, schema, table, columns)

    if table.fallback:
        await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
            "postgres/create_view_function",
            mapping=bq_function_mapping(schema, table, columns),
        )

        await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
            "postgres/create_view",
            mapping=boundary_view_mapping(
                schema,
                f"{table.table_name}_bq",
                f"{table.table_name}_bq_fn",
                columns,
            ),
        )

        await grant_view_select(pg_conn, schema, f"{table.table_name}_bq")


async def reconcile_views(pg_conn: Postgres, config: SyncConfig) -> bool:
    """Reconcile configured PostgreSQL views and report whether their set changed."""
    desired = desired_views(config)
    existing = await existing_views(pg_conn, list(config.schemas))
    desired_rpc = desired_functions(config)
    existing_rpc = await existing_functions(pg_conn, list(config.schemas))
    changed = existing != desired or existing_rpc != desired_rpc

    await drop_removed_views(pg_conn, existing - desired)
    await drop_removed_functions(pg_conn, existing_rpc - desired_rpc)

    for schema_name, schema_config in config.schemas.items():
        for table in schema_config.tables:
            await create_table_views(pg_conn, schema_name, table)
        await create_current_snapshot_function(pg_conn, schema_name)

    await pg_conn.commit()
    return changed
