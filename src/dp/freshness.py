"""Freshness metadata operations for published data."""

from psycopg import Connection
from psycopg.sql import Identifier
from whenever import Instant

from .models import SyncPlan, TableConfig
from .templates import load_template


def upsert_freshness(
    pg_conn: Connection,
    table: TableConfig,
    partition: str | None,
    attempted_at: Instant,
    *,
    success: bool,
) -> None:
    """Record one successful publication or failed attempt."""
    attempted_datetime = attempted_at.to_stdlib()
    updated_at = attempted_datetime if success else None
    pg_conn.execute(
        load_template(
            {
                "path": "pg/upsert_freshness",
                "mapping": {"schema": Identifier(table.resolved_schema)},
            }
        ).encode(),
        (
            table.table_name,
            table.strategy.value,
            partition,
            updated_at,
            attempted_datetime,
            "success" if success else "failure",
        ),
    )


def delete_freshness(pg_conn: Connection, table: TableConfig, partition: str) -> None:
    """Delete freshness for one removed partition."""
    pg_conn.execute(
        load_template(
            {
                "path": "pg/delete_partition_freshness",
                "mapping": {"schema": Identifier(table.resolved_schema)},
            }
        ).encode(),
        (table.table_name, table.strategy.value, partition),
    )


def delete_table_freshness(pg_conn: Connection, table: TableConfig) -> None:
    """Delete all freshness rows for one table."""
    pg_conn.execute(
        load_template(
            {
                "path": "pg/delete_table_freshness",
                "mapping": {"schema": Identifier(table.resolved_schema)},
            }
        ).encode(),
        (table.table_name,),
    )


def update_published_freshness(
    pg_conn: Connection,
    table: TableConfig,
    plan: SyncPlan,
    failed_partitions: set[str],
    attempted_at: Instant,
) -> None:
    """Update freshness to match one published table."""
    partitioned = plan.partitioned_tables.get(table.name)
    if partitioned is None:
        delete_table_freshness(pg_conn, table)
        upsert_freshness(pg_conn, table, None, attempted_at, success=True)
        return

    if partitioned.full_rebuild:
        delete_table_freshness(pg_conn, table)
        successful = set(partitioned.current_partitions)
    else:
        successful = set(partitioned.changed_paths)

    for partition_id in successful:
        upsert_freshness(pg_conn, table, partition_id, attempted_at, success=True)

    for partition_id in failed_partitions:
        upsert_freshness(pg_conn, table, partition_id, attempted_at, success=False)

    for partition_id in partitioned.removed_partitions:
        delete_freshness(pg_conn, table, partition_id)


def record_table_failure(
    pg_conn: Connection,
    table: TableConfig,
    plan: SyncPlan,
    attempted_at: Instant,
) -> None:
    """Record that one table did not publish in this attempt."""
    partitioned = plan.partitioned_tables.get(table.name)
    partitions = set(partitioned.changed_paths) if partitioned else {None}
    with pg_conn.transaction():
        for partition in partitions:
            upsert_freshness(pg_conn, table, partition, attempted_at, success=False)
