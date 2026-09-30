"""Step functions for source view reconciliation."""

from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from ..catalog import CatalogPaths
from ..constants import PROTECTED_VIEW_NAMES
from ..executor import Executor
from ..models import SyncConfig, TableConfig
from ..postgres import Postgres
from ..settings import settings
from ..types import PostgresParams
from .sources import sources
from .utils import (
    boundary_view_mapping,
    column_types_from_duckdb,
    ducklake_function_mapping,
    source_function_mapping,
    table_changes_function_mapping,
    table_function_mapping,
)


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
    """Return the configured table views."""
    return {
        (schema_name, table.table_name)
        for schema_name, schema_config in config.schemas.items()
        for table in schema_config.tables
    }


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


async def drop_view_objects(
    pg_conn: Postgres, schema_name: str, view_name: str
) -> None:
    """Drop one table view, its table function, and every source helper."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/drop_ducklake_objects",
        mapping={
            "schema": Identifier(schema_name),
            "view": Identifier(view_name),
            "function": Identifier(f"{view_name}_fn"),
            "helpers": [
                Identifier(f"{view_name}_{source.suffix}") for source in sources.all()
            ],
        },
    )


async def drop_removed_views(pg_conn: Postgres, removed: set[tuple[str, str]]) -> None:
    """Drop functions and views for names no longer in the sync config."""
    for schema_name, view_name in removed:
        if view_name not in PROTECTED_VIEW_NAMES:
            await drop_view_objects(pg_conn, schema_name, view_name)


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


async def create_source_helpers(
    executor: Executor[PostgresParams, list[TupleRow]],
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> None:
    """Create the DuckLake helper and one helper per configured fallback source."""
    await executor.execute(
        "postgres/sources/adapters/ducklake",
        mapping=ducklake_function_mapping(schema, table, columns),
    )

    for name in table.fallbacks:
        await executor.execute(
            f"postgres/sources/adapters/{name}",
            mapping=source_function_mapping(schema, table, columns, sources.get(name)),
        )


async def create_table_function(
    executor: Executor[PostgresParams, list[TupleRow]],
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> None:
    """Create the function behind one table view."""
    await executor.execute(
        "postgres/sources/create_function",
        mapping=table_function_mapping(schema, table, columns),
    )


async def create_boundary_view(
    executor: Executor[PostgresParams, list[TupleRow]],
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> None:
    """Create the boundary view that exposes the table function."""
    await executor.execute(
        "postgres/sources/create_view",
        mapping=boundary_view_mapping(
            schema, table.table_name, f"{table.table_name}_fn", columns
        ),
    )


async def create_table_views(
    pg_conn: Postgres, schema: str, table: TableConfig
) -> None:
    """Recreate one table view, its table function, and the private helpers.

    Dropping first lets a source column change alter the returned row type.
    """
    columns = await column_types_from_duckdb(pg_conn, table)

    if not columns:
        table_name = f"{table.resolved_schema}.{table.table_name}"
        raise RuntimeError(f"DuckDB returned no columns for table {table_name}")

    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)

    await drop_view_objects(pg_conn, schema, table.table_name)
    await drop_removed_functions(pg_conn, {(schema, changes_function_name(table))})
    await create_source_helpers(executor, schema, table, columns)
    await create_table_function(executor, schema, table, columns)
    await create_boundary_view(executor, schema, table, columns)
    await grant_view_select(pg_conn, schema, table.table_name)
    await create_table_changes_function(pg_conn, schema, table, columns)


async def reconcile_schema(
    pg_conn: Postgres, schema_name: str, config: SyncConfig
) -> None:
    """Reconcile every table and the snapshot function in one schema."""
    schema_config = config.schemas[schema_name]

    for table in schema_config.tables:
        await create_table_views(pg_conn, schema_name, table)

    await create_current_snapshot_function(pg_conn, schema_name)
