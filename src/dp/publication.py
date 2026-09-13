"""Shadow loading and atomic table publication operations."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import LiteralString, assert_never, cast

from psycopg import AsyncConnection
from psycopg.sql import SQL, Composable, Identifier, Literal
from whenever import Instant

from .authorization import bootstrap_table
from .conditions import partition_condition, scan_condition
from .executor import execute_sql
from .fallback import duckdb_type_for
from .freshness import (
    record_table_failures,
    update_published_freshness,
    upsert_freshness,
)
from .log import logger
from .models import (
    PartitionedTablePlan,
    PhysicalPartition,
    PublicationDecision,
    SyncConfig,
    SyncPlan,
    TableConfig,
)
from .templates import render_fragment
from .utils import atomic


async def table_exists(pg_conn: AsyncConnection, schema: str, table_name: str) -> bool:
    """Return whether one table already exists in the database."""
    cursor = await execute_sql(
        pg_conn,
        "postgres/table_exists",
        params=(schema, table_name),
    )
    row = await cursor.fetchone()

    return bool(row and row[0])


async def column_select_list(
    pg_conn: AsyncConnection, schema: str, table_name: str
) -> Composable:
    """Return a SQL select list with explicit casts for one table's columns."""
    cursor = await execute_sql(
        pg_conn,
        "postgres/column_types",
        params=(schema, table_name),
    )
    rows = cast("list[tuple[object, object]]", await cursor.fetchall())
    rows = [(str(column), str(column_type)) for column, column_type in rows]

    return SQL(", ").join(
        SQL("r[{}]::{} AS {}").format(
            Literal(col),
            SQL(duckdb_type_for(typ)),
            Identifier(col),
        )
        for col, typ in rows
    )


async def load_partition(
    pg_conn: AsyncConnection,
    schema: str,
    table_name: str,
    path: str,
    select_list: Composable,
    predicate: Composable,
) -> None:
    """Load one Parquet partition into a table."""
    await execute_sql(
        pg_conn,
        "postgres/load_partition",
        mapping={
            "temp": Identifier("_load_partition"),
            "cols": select_list,
            "path": Literal(path),
            "predicate": predicate,
            "schema": Identifier(schema),
            "table": Identifier(table_name),
        },
    )


async def cast_json_columns_to_jsonb(
    conn: AsyncConnection,
    schema: str,
    table_name: str,
) -> None:
    """Alter every json column on a table to jsonb before loading data.

    PostgreSQL rewrites the whole table for each type change, so every column
    travels in one statement. That costs one rewrite instead of one per column.
    """
    cursor = await execute_sql(
        conn,
        "postgres/json_columns",
        params=(schema, table_name),
    )
    rows = await cursor.fetchall()

    columns = [str(cast(object, row[0])) for row in rows]

    if not columns:
        return

    clauses = SQL(", ").join(
        render_fragment(
            "postgres/alter_json_to_jsonb_clause",
            {"column": Identifier(column)},
        )
        for column in columns
    )

    await execute_sql(
        conn,
        "postgres/cast_json_to_jsonb",
        mapping={
            "schema": Identifier(schema),
            "table": Identifier(table_name),
            "clauses": clauses,
        },
    )


async def create_indexes(
    conn: AsyncConnection, table: TableConfig, table_name: str
) -> None:
    """Create every configured index on a table."""
    for index in table.indexes:
        method = "" if index.method == "btree" else f" USING {index.method}"

        if index.expressions is not None:
            columns = SQL(", ").join(
                SQL(cast(LiteralString, expr)) for expr in index.expressions
            )
        else:
            columns = SQL(", ").join(Identifier(column) for column in index.columns)

        await execute_sql(
            conn,
            "postgres/create_index",
            mapping={
                "name": Identifier(index.name),
                "schema": Identifier(table.resolved_schema),
                "table": Identifier(table_name),
                "method": SQL(method),
                "columns": columns,
            },
        )


async def publish_table(conn: AsyncConnection, table: TableConfig) -> None:
    """Create indexes on the shadow table and atomically swap it into service."""
    table_name = table.table_name
    shadow_name = f"{table_name}__next"
    await create_indexes(conn, table, shadow_name)

    await execute_sql(
        conn,
        "postgres/swap_table",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table_name),
            "next_table": Identifier(shadow_name),
            "old_table": Identifier(f"{table_name}__old"),
        },
    )


async def delete_partitions(
    pg_conn: AsyncConnection,
    table: TableConfig,
    affected: list[PhysicalPartition],
) -> None:
    """Delete rows in affected partitions from the live table."""
    predicates = [partition_condition(partition) for partition in affected]
    await execute_sql(
        pg_conn,
        "postgres/delete_partitions",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table.table_name),
            "affected_partitions": SQL(" OR ").join(predicates),
        },
    )


async def append_batch(
    pg_conn: AsyncConnection,
    table: TableConfig,
    table_name: str,
    s3_path: str,
    select_list: Composable,
) -> None:
    """Append one Parquet file to an existing table."""
    await execute_sql(
        pg_conn,
        "postgres/append_batch",
        mapping={
            "temp": Identifier("_load_batch"),
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table_name),
            "s3_path": Literal(s3_path),
            "cols": select_list,
        },
    )


async def create_table_from_parquet(
    pg_conn: AsyncConnection,
    table: TableConfig,
    table_name: str,
    s3_path: str,
) -> None:
    """Create and populate a table from a Parquet file."""
    await execute_sql(
        pg_conn,
        "postgres/create_table_from_parquet",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table_name),
            "s3_path": Literal(s3_path),
        },
    )


async def load_table(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    table: TableConfig,
    table_name: str,
    paths: Sequence[str],
) -> None:
    """Create one table from the batch files and secure it, in one transaction.

    Every batch file of the run lands in the same table, so the table holds the
    whole content when the transaction commits. The transaction is explicit,
    because pgduckdb refuses to run inside a savepoint.
    """
    schema_config = config.schemas.get(table.resolved_schema)

    async with atomic(pg_conn):
        await create_table_from_parquet(pg_conn, table, table_name, paths[0])
        select_list = await column_select_list(
            pg_conn, table.resolved_schema, table_name
        )

        await bootstrap_table(
            pg_conn,
            table.resolved_schema,
            table_name,
            table.rls,
            schema_config.claim if schema_config else None,
        )

        for path in paths[1:]:
            await append_batch(pg_conn, table, table_name, path, select_list)

        await cast_json_columns_to_jsonb(pg_conn, table.resolved_schema, table_name)


async def rebuild_table(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    table: TableConfig,
    paths: Sequence[str],
) -> None:
    """Replace the live table with the batch files and create its indexes."""
    await load_table(pg_conn, config, table, table.table_name, paths)

    async with atomic(pg_conn):
        await create_indexes(pg_conn, table, table.table_name)


async def replace_partitions(
    pg_conn: AsyncConnection,
    table: TableConfig,
    table_plan: PartitionedTablePlan,
) -> None:
    """Replace every changed partition and drop every removed one in one transaction.

    The order of the partitions does not matter. One transaction moves the whole
    table from the old state to the new state at once.
    """
    select_list = await column_select_list(
        pg_conn, table.resolved_schema, table.table_name
    )

    affected = [
        table_plan.current_partitions[partition_id]
        for partition_id in table_plan.changed_paths
    ]
    affected.extend(table_plan.removed_partitions.values())

    async with atomic(pg_conn):
        if affected:
            await delete_partitions(pg_conn, table, affected)

        for partition_id, path in table_plan.changed_paths.items():
            partition = table_plan.current_partitions[partition_id]
            await load_partition(
                pg_conn,
                table.resolved_schema,
                table.table_name,
                path,
                select_list,
                scan_condition(partition),
            )


async def prepare_table(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    table: TableConfig,
    plan: SyncPlan,
    partitioned: PartitionedTablePlan | None,
) -> PreparedTable:
    """Prepare one table and report whether a shadow table waits for the swap.

    A partitioned table that only has changed partitions replaces them in place.
    Every other existing table loads a shadow table, because a full rebuild must
    keep the live table readable until the swap. The first creation writes the
    live table directly, because there is no reader to protect yet.
    """
    paths = planned_paths(plan, table.name, partitioned)
    exists = await table_exists(pg_conn, table.resolved_schema, table.table_name)
    route = decide_route(exists, partitioned)

    if route is PublicationRoute.REPLACE_PARTITIONS:
        if not exists:
            message = (
                f"Missing table {table.resolved_schema}.{table.table_name} "
                "for an incremental plan"
            )
            raise RuntimeError(message)

        assert partitioned is not None
        await replace_partitions(pg_conn, table, partitioned)
        return PreparedTable(table=table, swap=False)

    if route is PublicationRoute.SHADOW_SWAP:
        await load_table(pg_conn, config, table, f"{table.table_name}__next", paths)
        return PreparedTable(table=table, swap=True)

    await rebuild_table(pg_conn, config, table, paths)
    return PreparedTable(table=table, swap=False)


async def prepare_tables(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    plan: SyncPlan,
    changed: set[str],
) -> list[PreparedTable]:
    """Prepare, secure, and load each eligible table."""
    prepared: list[PreparedTable] = []

    for table in config.tables:
        if table.name not in changed:
            continue

        partitioned = plan.partitioned_tables.get(table.name)
        paths = planned_paths(plan, table.name, partitioned)

        logger.info(
            "Table preparation started table=%s path_count=%d",
            table.name,
            len(paths),
        )

        try:
            prepared_table = await prepare_table(
                pg_conn, config, table, plan, partitioned
            )
        except Exception:
            logger.exception("Table preparation failed table=%s", table.name)
            continue

        logger.info("Table preparation completed table=%s", table.name)
        prepared.append(prepared_table)

    return prepared


async def publish_prepared_tables(
    pg_conn: AsyncConnection,
    prepared: Sequence[PreparedTable],
    plan: SyncPlan,
    failed_partitions: dict[str, set[str]],
    attempted_at: Instant,
) -> set[str]:
    """Publish prepared tables and return those that succeeded.

    Each table commits its own work, so a failure in one table keeps the tables
    that published before it.
    """
    published: set[str] = set()

    for prepared_table in prepared:
        table = prepared_table.table
        logger.info("Table publication started table=%s", table.name)

        try:
            if prepared_table.swap:
                await publish_table(pg_conn, table)

            await update_published_freshness(
                pg_conn,
                table,
                plan,
                failed_partitions.get(table.name, set()),
                attempted_at,
            )
        except Exception:
            logger.exception("Table publication failed table=%s", table.name)
            await pg_conn.rollback()

            await record_table_failures(pg_conn, [table], plan, attempted_at)
            await upsert_freshness(
                pg_conn,
                table,
                failed_partitions.get(table.name, set()),
                attempted_at,
                success=False,
            )
            await pg_conn.commit()

            continue

        await pg_conn.commit()

        logger.info("Table publication completed table=%s", table.name)
        published.add(table.name)

    return published


class PublicationRoute(StrEnum):
    """How one table reaches its published state."""

    CREATE = "create"
    REPLACE_PARTITIONS = "replace_partitions"
    SHADOW_SWAP = "shadow_swap"


@dataclass(frozen=True, slots=True)
class PreparedTable:
    """One prepared table and whether a shadow table waits for the swap."""

    table: TableConfig
    swap: bool


def failed_partition_ids(
    table_plan: PartitionedTablePlan, failed_paths: set[str]
) -> set[str]:
    """Return partition IDs for failed paths in one table plan."""
    return {
        partition_id
        for partition_id, path in table_plan.changed_paths.items()
        if path in failed_paths
    }


def apply_partition_fallback(
    table_plan: PartitionedTablePlan, failed_ids: set[str]
) -> None:
    """Keep prior manifest entries and omit failed new entries."""
    for partition_id in failed_ids:
        table_plan.changed_paths.pop(partition_id)
        previous = table_plan.previous_partitions.get(partition_id)

        if previous is None:
            table_plan.current_partitions.pop(partition_id, None)
            continue

        table_plan.current_partitions[partition_id] = previous


def reduce_sync_plan(plan: SyncPlan, failed_paths: set[str]) -> PublicationDecision:
    """Return the publishable plan and its extraction failures."""
    reduced = plan.model_copy(deep=True)
    blocked = {
        table for table, paths in plan.paths.items() if failed_paths.intersection(paths)
    }
    failed_partitions: dict[str, set[str]] = {}

    for table, table_plan in reduced.partitioned_tables.items():
        failed_ids = failed_partition_ids(table_plan, failed_paths)

        if not failed_ids:
            continue

        failed_partitions[table] = failed_ids

        if table_plan.full_rebuild:
            blocked.add(table)
        else:
            apply_partition_fallback(table_plan, failed_ids)

    return PublicationDecision(
        plan=reduced,
        blocked_tables=blocked,
        failed_partitions=failed_partitions,
    )


def planned_paths(
    plan: SyncPlan,
    table: str,
    partitioned: PartitionedTablePlan | None,
) -> list[str]:
    """Return ordinary or batch Parquet paths for one table.

    A batch holds many partitions, so the paths of a partitioned table repeat.
    The insertion order of the plan keeps the batches in publication order.
    """
    match partitioned:
        case PartitionedTablePlan():
            return list(dict.fromkeys(partitioned.changed_paths.values()))
        case None:
            return plan.paths.get(table, [])
        case _:
            assert_never(partitioned)


def decide_route(
    exists: bool, partitioned: PartitionedTablePlan | None
) -> PublicationRoute:
    """Choose the publication route for one table."""
    if isinstance(partitioned, PartitionedTablePlan) and not partitioned.full_rebuild:
        return PublicationRoute.REPLACE_PARTITIONS

    if exists:
        return PublicationRoute.SHADOW_SWAP

    return PublicationRoute.CREATE
