"""Freshness metadata operations for published data."""

from collections.abc import Collection, Mapping

from psycopg.sql import Identifier
from whenever import Instant

from .models import SyncPlan, TableConfig
from .protocols import PostgresExecutor, PostgresFreshness
from .templates import TemplateSpec, load_template


def upsert_freshness(
    pg_conn: PostgresFreshness,
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

    with pg_conn.cursor() as cursor:
        cursor.executemany(
            load_template(
                TemplateSpec(
                    path="pg/upsert_freshness",
                    mapping={"schema": Identifier(table.resolved_schema)},
                )
            ).encode(),
            [
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


def delete_freshness(
    pg_conn: PostgresFreshness, table: TableConfig, partitions: Collection[str]
) -> None:
    """Delete freshness for removed partitions."""
    if not partitions:
        return

    with pg_conn.cursor() as cursor:
        cursor.executemany(
            load_template(
                TemplateSpec(
                    path="pg/delete_partition_freshness",
                    mapping={"schema": Identifier(table.resolved_schema)},
                )
            ).encode(),
            [
                (table.table_name, table.strategy.value, partition)
                for partition in partitions
            ],
        )


def delete_table_freshness(pg_conn: PostgresExecutor, table: TableConfig) -> None:
    """Delete all freshness rows for one table."""
    pg_conn.execute(
        load_template(
            TemplateSpec(
                path="pg/delete_table_freshness",
                mapping={"schema": Identifier(table.resolved_schema)},
            )
        ).encode(),
        (table.table_name,),
    )


def update_published_freshness(
    pg_conn: PostgresFreshness,
    table: TableConfig,
    plan: SyncPlan,
    failed_partitions: set[str],
    attempted_at: Instant,
) -> None:
    """Update freshness to match one published table."""
    partitioned = plan.partitioned_tables.get(table.name)

    if partitioned is None:
        delete_table_freshness(pg_conn, table)
        upsert_freshness(pg_conn, table, {None}, attempted_at, success=True)
        return

    if partitioned.full_rebuild:
        delete_table_freshness(pg_conn, table)
        successful = partitioned.current_partitions.keys()
    else:
        successful = partitioned.changed_paths.keys()

    if successful:
        upsert_freshness(pg_conn, table, successful, attempted_at, success=True)

    if failed_partitions:
        upsert_freshness(pg_conn, table, failed_partitions, attempted_at, success=False)

    if partitioned.removed_partitions:
        delete_freshness(pg_conn, table, partitioned.removed_partitions.keys())


def record_table_failures(
    pg_conn: PostgresFreshness,
    tables: list[TableConfig],
    plan: SyncPlan,
    attempted_at: Instant,
    partitions_by_table: Mapping[str, Collection[str | None]] | None = None,
) -> None:
    """Record failed publication for a batch of tables."""
    if not tables:
        return

    attempted_datetime = attempted_at.to_stdlib()
    rows: list[tuple[object, ...]] = []

    for table in tables:
        partitioned = plan.partitioned_tables.get(table.name)
        partitions = (
            partitions_by_table[table.name]
            if partitions_by_table and table.name in partitions_by_table
            else partitioned.changed_paths.keys()
            if partitioned
            else {None}
        )

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
    with pg_conn.transaction(), pg_conn.cursor() as cursor:
        cursor.executemany(
            load_template(
                TemplateSpec(
                    path="pg/upsert_freshness",
                    mapping={"schema": Identifier(tables[0].resolved_schema)},
                )
            ).encode(),
            rows,
        )
