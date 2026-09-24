"""Freshness metadata operations for published data."""

from collections.abc import Collection, Mapping

from psycopg import AsyncConnection
from psycopg.sql import Identifier
from whenever import Instant

from .executor import SQLParam, execute_sql
from .models import PartitionedTablePlan, SyncPlan, TableConfig
from .utils import atomic


async def upsert_freshness(
    pg_conn: AsyncConnection,
    table: TableConfig,
    partitions: Collection[str | None],
    attempted_at: Instant,
    *,
    success: bool,
) -> None:
    """Record publication results for a set of partitions."""
    if not partitions:
        return

    attempted_datetime = attempted_at.to_stdlib()
    updated_at = attempted_datetime if success else None

    async with pg_conn.cursor() as cursor:
        await execute_sql(
            cursor,
            "postgres/upsert_freshness",
            mapping={"schema": Identifier(table.resolved_schema)},
            params=[
                (
                    table.table_name,
                    table.strategy.value,
                    partition,
                    updated_at,
                    attempted_datetime,
                    "success" if success else "failure",
                )
                for partition in partitions
            ],
        )


async def delete_partition_freshness(
    pg_conn: AsyncConnection, table: TableConfig, partitions: Collection[str]
) -> None:
    """Delete freshness for removed partitions."""
    if not partitions:
        return

    async with pg_conn.cursor() as cursor:
        await execute_sql(
            cursor,
            "postgres/delete_freshness",
            mapping={
                "schema": Identifier(table.resolved_schema),
                "partition": True,
            },
            params=[
                (table.table_name, table.strategy.value, partition)
                for partition in partitions
            ],
        )


async def delete_table_freshness(pg_conn: AsyncConnection, table: TableConfig) -> None:
    """Delete all freshness rows for one table."""
    await execute_sql(
        pg_conn,
        "postgres/delete_freshness",
        mapping={"schema": Identifier(table.resolved_schema)},
        params=(table.table_name,),
    )


async def update_published_freshness(
    pg_conn: AsyncConnection,
    table: TableConfig,
    plan: SyncPlan,
    failed_partitions: set[str],
    attempted_at: Instant,
) -> None:
    """Update freshness to match one published table."""
    partitioned = plan.partitioned_tables.get(table.name)

    if partitioned is None:
        await delete_table_freshness(pg_conn, table)
        await upsert_freshness(pg_conn, table, {None}, attempted_at, success=True)
        return

    if partitioned.full_rebuild:
        await delete_table_freshness(pg_conn, table)
        successful = partitioned.current_partitions.keys()
    else:
        successful = partitioned.changed_paths.keys()

    if successful:
        await upsert_freshness(pg_conn, table, successful, attempted_at, success=True)

    if failed_partitions:
        await upsert_freshness(
            pg_conn, table, failed_partitions, attempted_at, success=False
        )

    if partitioned.removed_partitions:
        await delete_partition_freshness(
            pg_conn, table, partitioned.removed_partitions.keys()
        )


def failure_partitions(
    table: TableConfig,
    partitioned: PartitionedTablePlan | None,
    partitions_by_table: Mapping[str, Collection[str | None]] | None,
) -> Collection[str | None]:
    """Return the partitions to mark errored for one table."""
    if partitions_by_table is not None:
        explicit = partitions_by_table.get(table.name)
        if explicit is not None:
            return explicit

    if partitioned:
        return partitioned.changed_paths.keys()

    return {None}


async def record_freshness_failures(
    pg_conn: AsyncConnection,
    tables: list[TableConfig],
    plan: SyncPlan,
    attempted_at: Instant,
    partitions_by_table: Mapping[str, Collection[str | None]] | None = None,
) -> None:
    """Record errored publication for a batch of tables."""
    if not tables:
        return

    attempted_datetime = attempted_at.to_stdlib()
    rows: list[tuple[SQLParam, ...]] = []

    for table in tables:
        partitioned = plan.partitioned_tables.get(table.name)
        partitions = failure_partitions(table, partitioned, partitions_by_table)

        rows.extend(
            (
                table.table_name,
                table.strategy.value,
                partition,
                None,
                attempted_datetime,
                "failure",
            )
            for partition in partitions
        )

    async with atomic(pg_conn), pg_conn.cursor() as cursor:
        await execute_sql(
            cursor,
            "postgres/upsert_freshness",
            mapping={"schema": Identifier(tables[0].resolved_schema)},
            params=rows,
        )
