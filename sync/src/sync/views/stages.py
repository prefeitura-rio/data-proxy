"""Step functions for source view reconciliation."""

from typing import TYPE_CHECKING, Final

from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from sync.constants import PROTECTED_VIEW_NAMES
from sync.ducklake import data_path
from sync.executor import Executor
from sync.models import PartitionedTable, SyncConfig, TableConfig
from sync.schema import schema as app_schema
from sync.settings import settings
from sync.sources.registry import sources
from sync.types import PostgresParams
from sync.utils import destination_name

from .mappings import (
    boundary_view_mapping,
    column_types_from_duckdb,
    ducklake_function_mapping,
    meta_mapping,
    serving_definition_signature,
    source_function_mapping,
    table_function_mapping,
)

if TYPE_CHECKING:
    from sync.postgres import Postgres


SCHEMA_FUNCTIONS: Final = {
    "snapshot": "()",
    "files": "(text)",
    "agg": "(text)",
    "hll": "(text, text, text, integer)",
    "changes": "(text, bigint, bigint)",
}


def desired_functions(
    config: SyncConfig, schema_names: set[str] | None = None
) -> set[tuple[str, str]]:
    """Return the managed functions of the selected schemas, which are the same for each schema."""
    selected = set(config.schemas) if schema_names is None else schema_names
    return {
        (schema_name, name) for schema_name in selected for name in SCHEMA_FUNCTIONS
    }


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
    pg_conn: Postgres, schema_names: list[str], desired: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Return the managed functions that exist in the configured schemas."""
    rows = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/list_functions",
        mapping={"schemas": Literal(schema_names)},
        expect=tuple[str, str],
    )
    return {(schema, name) for schema, name in rows if (schema, name) in desired}


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
    """Drop the managed functions of a schema that leaves the configuration."""
    for schema_name, function_name in removed:
        await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
            "postgres/drop_function",
            mapping={
                "schema": Identifier(schema_name),
                "function": Identifier(function_name),
                "arguments": SCHEMA_FUNCTIONS[function_name],
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
            "app_schema": app_schema(),
            "schema_name": Literal(schema_name),
            "table_name": Literal(view_name),
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


async def create_schema_rpc(pg_conn: Postgres, schema: str) -> None:
    """Create the wrappers that call the generic functions of the routing schema for one data schema."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_schema_rpc",
        mapping={
            "schema": Identifier(schema),
            "schema_literal": Literal(schema),
            "app_schema": app_schema(),
            "user_role": Identifier(settings.AUTH_USER_ROLE),
        },
    )


async def create_snapshot(pg_conn: Postgres, schema: str) -> None:
    """Create the schema-scoped current snapshot function."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_snapshot",
        mapping={
            "schema": Identifier(schema),
            "function": Identifier("snapshot"),
            "metadata_schema": schema,
            "data_path": data_path(schema),
            "user_role": Identifier(settings.AUTH_USER_ROLE),
        },
    )


async def create_table_files_function(pg_conn: Postgres, schema: str) -> None:
    """Create the schema-scoped function that lists the Parquet files of a table."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/create_table_files_function",
        mapping={
            "schema": Identifier(schema),
            "function": Identifier("files"),
            "metadata_schema": schema,
            "data_path": data_path(schema),
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


async def write_meta(
    executor: Executor[PostgresParams, list[TupleRow]],
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> None:
    """Write the facts that the generic functions read for this table."""
    await executor.execute(
        "postgres/write_meta",
        mapping=meta_mapping(schema, table, columns, claim),
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
    if len(columns) == 0:
        table_name = destination_name(table.resolved_schema, table.table_name)
        raise RuntimeError(f"DuckDB returned no columns for table {table_name}")

    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)

    await drop_view_objects(pg_conn, schema, table.table_name)
    await create_source_helpers(executor, schema, table, columns)
    await create_table_function(executor, schema, table, columns, claim)
    await write_meta(executor, schema, table, columns, claim)
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
        if signatures.get(key) == signature:
            continue
        await create_table_views(
            pg_conn, schema_name, table, columns, schema_config.claim
        )
        changed = True

    if changed or not {(schema_name, name) for name in SCHEMA_FUNCTIONS} <= functions:
        await create_snapshot(pg_conn, schema_name)
        await create_table_files_function(pg_conn, schema_name)
        await create_schema_rpc(pg_conn, schema_name)
        changed = True

    return changed
