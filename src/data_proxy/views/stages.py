"""Step functions for source view reconciliation."""

from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from ..catalog import CatalogPaths
from ..constants import PROTECTED_VIEW_NAMES
from ..executor import Executor
from ..models import PartitionedTable, SyncConfig, TableConfig
from ..postgres import Postgres
from ..settings import settings
from ..sources.registry import sources
from ..types import PostgresParams
from .mappings import (
    boundary_view_mapping,
    column_types_from_duckdb,
    ducklake_function_mapping,
    serving_definition_signature,
    source_function_mapping,
    table_changes_function_mapping,
    table_function_mapping,
)


def changes_function_name(table: TableConfig) -> str:
    """Return the managed change-feed function name for one table."""
    return f"ducklake_changes_{table.table_name}"


def desired_functions(
    config: SyncConfig, schema_names: set[str] | None = None
) -> set[tuple[str, str]]:
    """Return managed change-feed and snapshot functions for selected schemas."""
    selected = set(config.schemas) if schema_names is None else schema_names
    functions = {(schema_name, "ducklake_latest_snapshot") for schema_name in selected}
    functions.update(
        (schema_name, changes_function_name(table))
        for schema_name, schema_config in config.schemas.items()
        if schema_name in selected
        for table in schema_config.tables
    )
    return functions


def desired_views(
    config: SyncConfig, schema_names: set[str] | None = None
) -> set[tuple[str, str]]:
    """Return configured table views for selected schemas."""
    selected = set(config.schemas) if schema_names is None else schema_names
    return {
        (schema_name, table.table_name)
        for schema_name, schema_config in config.schemas.items()
        if schema_name in selected
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


async def existing_view_signatures(
    pg_conn: Postgres, schema_names: list[str]
) -> dict[tuple[str, str], str | None]:
    """Return generated definition signatures stored on managed views."""
    rows = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/list_view_signatures",
        mapping={"schemas": Literal(schema_names)},
        expect=tuple[str, str, str | None],
    )
    return {(schema, name): signature for schema, name, signature in rows}


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
        "postgres/drop_view_objects",
        mapping={
            "schema": Identifier(schema_name),
            "view": Identifier(view_name),
            "function": Identifier(f"{view_name}_fn"),
            "helpers": [
                Identifier(f"{view_name}_dl_fn"),
                *[
                    Identifier(f"{view_name}_{source.fallback.suffix}")
                    for source in sources.all()
                    if source.fallback is not None
                ],
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
    claim: str | None,
) -> None:
    """Create one typed DuckLake change-feed function."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_table_changes_function",
        mapping=table_changes_function_mapping(schema, table, columns, claim),
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
    """Create the DuckLake helper and optional configured-source fallback helper."""
    await executor.execute(
        "postgres/views/ducklake",
        mapping=ducklake_function_mapping(schema, table, columns),
    )

    if isinstance(table, PartitionedTable) and table.fallback:
        source = sources.configure(
            table.resolved_source, table.resolved_source_settings
        )
        fallback = source.fallback
        if fallback is None:  # pragma: no cover
            raise RuntimeError(f"Source {source.name!r} has no fallback helper")
        await executor.execute(
            fallback.template,
            mapping=source_function_mapping(schema, table, columns, source),
        )


async def create_table_function(
    executor: Executor[PostgresParams, list[TupleRow]],
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> None:
    """Create the function behind one table view."""
    await executor.execute(
        "postgres/views/create_function",
        mapping=table_function_mapping(schema, table, columns, claim),
    )


async def create_boundary_view(
    executor: Executor[PostgresParams, list[TupleRow]],
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> None:
    """Create the boundary view that exposes the table function."""
    await executor.execute(
        "postgres/views/create_view",
        mapping=boundary_view_mapping(
            schema, table.table_name, f"{table.table_name}_fn", columns
        ),
    )


async def create_table_views(
    pg_conn: Postgres,
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> None:
    """Recreate one table view, its table function, and the private helpers.

    Dropping first lets a source column change alter the returned row type.
    """
    if not columns:
        table_name = f"{table.resolved_schema}.{table.table_name}"
        raise RuntimeError(f"DuckDB returned no columns for table {table_name}")

    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)

    await drop_view_objects(pg_conn, schema, table.table_name)
    await drop_removed_functions(pg_conn, {(schema, changes_function_name(table))})
    await create_source_helpers(executor, schema, table, columns)
    await create_table_function(executor, schema, table, columns, claim)
    await create_boundary_view(executor, schema, table, columns)

    await executor.execute(
        "postgres/write_view_signature",
        mapping={
            "schema": Identifier(schema),
            "view": Identifier(table.table_name),
            "signature": Literal(
                serving_definition_signature(schema, table, columns, claim)
            ),
        },
    )
    await grant_view_select(pg_conn, schema, table.table_name)
    await create_table_changes_function(pg_conn, schema, table, columns, claim)


async def reconcile_schema(
    pg_conn: Postgres,
    schema_name: str,
    config: SyncConfig,
    signatures: dict[tuple[str, str], str | None],
    functions: set[tuple[str, str]],
    table_names: set[str] | None,
) -> bool:
    """Reconcile changed generated serving objects in one schema."""
    schema_config = config.schemas[schema_name]
    changed = False

    for table in schema_config.tables:
        if table_names is not None and table.name not in table_names:
            continue
        columns = await column_types_from_duckdb(pg_conn, table)
        signature = serving_definition_signature(
            schema_name, table, columns, schema_config.claim
        )
        key = (schema_name, table.table_name)
        change_function = (schema_name, changes_function_name(table))
        if signatures.get(key) == signature and change_function in functions:
            continue
        await create_table_views(
            pg_conn, schema_name, table, columns, schema_config.claim
        )
        changed = True

    snapshot_function = (schema_name, "ducklake_latest_snapshot")
    if changed or snapshot_function not in functions:
        await create_current_snapshot_function(pg_conn, schema_name)

    return changed
