"""Plan reduction, shadow loading, and atomic table publication operations."""

from collections.abc import Sequence
from typing import LiteralString, assert_never, cast

from duckdb import DuckDBPyConnection
from psycopg import Connection
from psycopg.sql import SQL, Identifier, Literal
from whenever import Instant

from dp.log import logger

from .authorization import bootstrap_table
from .extraction import selection_fields
from .freshness import (
    record_table_failures,
    update_published_freshness,
    upsert_freshness,
)
from .models import (
    PartitionedTablePlan,
    PhysicalPartition,
    PublicationDecision,
    RangeSelection,
    RemainderSelection,
    SyncConfig,
    SyncPlan,
    TableConfig,
    TimeRangeSelection,
)
from .settings import settings
from .templates import execute_sql, render_template


def load_table(
    conn: DuckDBPyConnection,
    schema: str,
    table_name: str,
    paths: list[str],
) -> None:
    """Load exact Parquet paths into a prepared PostgreSQL table."""
    for path in paths:
        execute_sql(
            conn,
            "duckdb/load_parquet",
            mapping={
                "schema": Identifier(schema),
                "table_name": Identifier(table_name),
                "gcs_path": Literal(path),
            },
        )


def load_partition(
    pg_conn: Connection,
    schema: str,
    table_name: str,
    path: str,
) -> None:
    """Load one Parquet partition into a table via pgduckdb read_parquet."""
    rows = cast(
        "list[tuple[str, str]]",
        execute_sql(
            pg_conn,
            "postgres/column_types",
            params=(schema, table_name),
        ).fetchall(),
    )

    select_list = SQL(", ").join(
        SQL("r[{}]::{} AS {}").format(
            Literal(col),
            SQL(cast(LiteralString, typ)),
            Identifier(col),
        )
        for col, typ in rows
    )

    execute_sql(
        pg_conn,
        "postgres/load_partition",
        mapping={
            "temp": Identifier("_load_partition"),
            "cols": select_list,
            "path": Literal(path),
            "schema": Identifier(schema),
            "table": Identifier(table_name),
        },
    )


def cast_json_columns_to_jsonb(
    conn: Connection,
    schema: str,
    table_name: str,
) -> None:
    """Alter every json column on a table to jsonb before loading data."""
    rows = execute_sql(
        conn,
        "postgres/json_columns",
        params=(schema, table_name),
    ).fetchall()

    for row in rows:
        column = cast(str, row[0])
        execute_sql(
            conn,
            "postgres/cast_json_to_jsonb",
            mapping={
                "schema": Identifier(schema),
                "table": Identifier(table_name),
                "column": Identifier(column),
            },
        )


def create_indexes(conn: Connection, table: TableConfig, table_name: str) -> None:
    """Create every configured index on a table."""
    for index in table.indexes:
        method = "" if index.method == "btree" else f" USING {index.method}"

        if index.expressions is not None:
            columns = SQL(", ").join(
                SQL(cast(LiteralString, expr)) for expr in index.expressions
            )
        else:
            columns = SQL(", ").join(Identifier(column) for column in index.columns)

        execute_sql(
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


def publish_table(conn: Connection, table: TableConfig) -> None:
    """Atomically swap one prepared shadow table into service."""
    table_name = table.table_name
    execute_sql(
        conn,
        "postgres/swap_table",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table_name),
            "next_table": Identifier(f"{table_name}__next"),
            "old_table": Identifier(f"{table_name}__old"),
        },
    )

    create_indexes(conn, table, table_name)


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
    """Return ordinary or changed-partition Parquet paths for one table."""
    match partitioned:
        case PartitionedTablePlan():
            return list(partitioned.changed_paths.values())
        case None:
            return plan.paths.get(table, [])
        case _:
            assert_never(partitioned)


def partition_predicate(partition: PhysicalPartition) -> SQL:
    """Return the SQL predicate that matches one partition."""
    mapping = selection_fields(partition.selection)

    match partition.selection:
        case RangeSelection() | TimeRangeSelection():
            path = "postgres/partition_range_predicate"
        case RemainderSelection():
            path = "postgres/partition_remainder_predicate"
        case _:  # pragma: no cover
            assert_never(partition.selection)

    return SQL(render_template(path, mapping, as_literal=True))


def delete_partitions(
    pg_conn: Connection,
    table: TableConfig,
    affected: list[PhysicalPartition],
) -> None:
    """Delete rows in affected partitions from the live table."""
    predicates = [partition_predicate(partition) for partition in affected]
    execute_sql(
        pg_conn,
        "postgres/delete_partitions",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table.table_name),
            "affected_partitions": SQL(" OR ").join(predicates),
        },
    )


def create_shadow_from_parquet(
    duckdb_conn: DuckDBPyConnection,
    table: TableConfig,
    shadow_name: str,
    paths: list[str],
) -> None:
    """Create an empty shadow table from the first Parquet schema."""
    if not paths:
        message = f"Parquet paths missing from sync plan: {table.name}"
        raise RuntimeError(message)

    execute_sql(
        duckdb_conn,
        "duckdb/create_table_from_parquet",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(shadow_name),
            "gcs_path": Literal(paths[0]),
        },
    )


def prepare_tables(
    pg_conn: Connection,
    duckdb_conn: DuckDBPyConnection,
    config: SyncConfig,
    plan: SyncPlan,
    changed: set[str],
) -> list[TableConfig]:
    """Prepare, secure, and load each eligible table."""
    needs_duckdb = any(
        plan.partitioned_tables.get(table.name) is None
        or plan.partitioned_tables[table.name].full_rebuild
        for table in config.tables
        if table.name in changed
    )

    if needs_duckdb:
        execute_sql(
            duckdb_conn,
            "duckdb/attach_postgres",
            mapping={"pg_dsn": Literal(settings.PG_DSN)},
        )

    prepared: list[TableConfig] = []

    for table in config.tables:
        if table.name not in changed:
            continue

        partitioned = plan.partitioned_tables.get(table.name)
        paths = planned_paths(plan, table.name, partitioned)
        shadow_name = f"{table.table_name}__next"

        logger.info(
            "Table preparation started table=%s path_count=%d",
            table.name,
            len(paths),
        )

        try:
            match partitioned:
                case PartitionedTablePlan() as table_plan if (
                    not table_plan.full_rebuild
                ):
                    pg_conn.autocommit = True
                    try:
                        for partition_id, path in table_plan.changed_paths.items():
                            single = table_plan.current_partitions[partition_id]
                            try:
                                with pg_conn.transaction():
                                    delete_partitions(pg_conn, table, [single])
                                    load_partition(
                                        pg_conn,
                                        table.resolved_schema,
                                        table.table_name,
                                        path,
                                    )
                            except Exception:
                                logger.exception(
                                    "Partition load failed table=%s partition=%s",
                                    table.name,
                                    partition_id,
                                )

                        for single in table_plan.removed_partitions.values():
                            try:
                                with pg_conn.transaction():
                                    delete_partitions(pg_conn, table, [single])
                            except Exception:
                                logger.exception(
                                    "Partition delete failed table=%s partition=%s",
                                    table.name,
                                    single.partition_id,
                                )
                    finally:
                        pg_conn.autocommit = False
                case _:
                    create_shadow_from_parquet(duckdb_conn, table, shadow_name, paths)
                    schema_config = config.schemas.get(table.resolved_schema)

                    with pg_conn.transaction():
                        bootstrap_table(
                            pg_conn,
                            table.resolved_schema,
                            shadow_name,
                            table.rls,
                            schema_config.claim if schema_config else None,
                        )

                    logger.info(
                        "Loading table table=%s path_count=%d",
                        table.name,
                        len(paths),
                    )
                    load_table(duckdb_conn, table.resolved_schema, shadow_name, paths)

                    with pg_conn.transaction():
                        cast_json_columns_to_jsonb(
                            pg_conn, table.resolved_schema, shadow_name
                        )
        except Exception:
            logger.exception("Table preparation failed table=%s", table.name)
            continue

        logger.info("Table preparation completed table=%s", table.name)
        prepared.append(table)

    return prepared


def publish_prepared_tables(
    pg_conn: Connection,
    prepared: Sequence[TableConfig],
    plan: SyncPlan,
    failed_partitions: dict[str, set[str]],
    attempted_at: Instant,
) -> set[str]:
    """Publish prepared tables and return those that succeeded."""
    published: set[str] = set()

    for table in prepared:
        logger.info("Table publication started table=%s", table.name)

        table_plan = plan.partitioned_tables.get(table.name)
        is_incremental = table_plan is not None and not table_plan.full_rebuild

        try:
            with pg_conn.transaction():
                if not is_incremental:
                    publish_table(pg_conn, table)

                update_published_freshness(
                    pg_conn,
                    table,
                    plan,
                    failed_partitions.get(table.name, set()),
                    attempted_at,
                )
        except Exception:
            logger.exception("Table publication failed table=%s", table.name)

            record_table_failures(pg_conn, [table], plan, attempted_at)

            with pg_conn.transaction():
                upsert_freshness(
                    pg_conn,
                    table,
                    failed_partitions.get(table.name, set()),
                    attempted_at,
                    success=False,
                )

            continue

        logger.info("Table publication completed table=%s", table.name)
        published.add(table.name)

    return published
