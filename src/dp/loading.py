"""Parquet-to-PostgreSQL loading and atomic publication operations."""

from collections.abc import Sequence
from typing import TypedDict

from loguru import logger
from psycopg import Connection
from psycopg.sql import SQL, Identifier, Literal

from .duckdb import DBConnection
from .models import (
    PartitionedTablePlan,
    PhysicalPartition,
    RlsConfig,
    SyncConfig,
    SyncPlan,
    TableConfig,
)
from .settings import settings
from .templates import load_template


class BootstrapInput(TypedDict):
    """Inputs required to configure one serving table."""

    schema: str
    table_name: str
    rls: RlsConfig | None


def bootstrap_table(pg_conn: Connection, params: BootstrapInput) -> None:
    """Apply table grants and optional row-level security."""
    schema = params["schema"]
    table_name = params["table_name"]
    rls = params["rls"]
    statements = [
        load_template(
            {
                "path": "pg/grant_select",
                "mapping": {
                    "schema": Identifier(schema),
                    "table": Identifier(table_name),
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                },
            }
        )
    ]

    if rls:
        statements.append(
            load_template(
                {
                    "path": "pg/enable_rls",
                    "mapping": {
                        "schema": Identifier(schema),
                        "table": Identifier(table_name),
                        "column": Identifier(rls.column),
                    },
                }
            )
        )

    pg_conn.execute(";".join(statements).encode())


def load_table(
    conn: DBConnection,
    schema: str,
    table_name: str,
    paths: list[str],
) -> None:
    """Load exact Parquet paths into a prepared PostgreSQL table."""
    for path in paths:
        conn.execute(
            load_template(
                {
                    "path": "duckdb/load_parquet",
                    "mapping": {
                        "schema": Identifier(schema),
                        "table_name": Identifier(table_name),
                        "gcs_path": Literal(path),
                    },
                }
            )
        )


def create_indexes(
    conn: Connection,
    table: TableConfig,
    table_name: str,
) -> None:
    """Create every configured index on a table."""
    for index in table.indexes:
        conn.execute(
            load_template(
                {
                    "path": "pg/create_index",
                    "mapping": {
                        "name": Identifier(index.name),
                        "schema": Identifier(table.resolved_schema),
                        "table": Identifier(table_name),
                        "columns": SQL(", ").join(
                            Identifier(column) for column in index.columns
                        ),
                    },
                }
            ).encode()
        )


def publish_table(
    conn: Connection,
    table: TableConfig,
) -> None:
    """Atomically swap one prepared shadow table into service."""
    table_name = table.table_name
    shadow_name = f"{table_name}__next"

    conn.execute(
        load_template(
            {
                "path": "pg/swap_table",
                "mapping": {
                    "schema": Identifier(table.resolved_schema),
                    "table": Identifier(table_name),
                    "next_table": Identifier(shadow_name),
                    "old_table": Identifier(f"{table_name}__old"),
                },
            }
        ).encode()
    )

    create_indexes(conn, table, table_name)


def configured_schemas(config: SyncConfig) -> set[str]:
    """Return distinct PostgreSQL schemas used by a config."""
    return {table.resolved_schema for table in config.tables}


def validate_sync_plan(config: SyncConfig, plan: SyncPlan) -> set[str]:
    """Validate planned tables and return their configured names."""
    changed = set(plan.signatures) | set(plan.partitioned_tables)
    configured = {table.bq_table for table in config.tables}
    unknown = changed - configured

    if unknown:
        message = f"Sync plan contains unknown tables: {sorted(unknown)}"
        raise RuntimeError(message)

    return changed


def initialize_schemas(pg_conn: Connection, config: SyncConfig) -> None:
    """Create roles and application schemas before publication."""
    pg_conn.execute(
        load_template(
            {
                "path": "pg/init_roles",
                "mapping": {
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                    "authenticator_role": Identifier(settings.AUTH_AUTHENTICATOR_ROLE),
                    "rls_schema": Identifier("rls"),
                },
            }
        ).encode()
    )
    for schema in configured_schemas(config):
        pg_conn.execute(
            load_template(
                {
                    "path": "pg/init_schema",
                    "mapping": {
                        "schema": Identifier(schema),
                        "user_role": Identifier(settings.AUTH_USER_ROLE),
                    },
                }
            ).encode()
        )
    pg_conn.commit()


def table_exists(pg_conn: Connection, schema: str, table: str) -> bool:
    """Return whether one PostgreSQL serving table exists."""
    cursor = pg_conn.execute(
        load_template(
            {
                "path": "pg/table_exists",
                "mapping": {"qualified_table": Literal(f"{schema}.{table}")},
            }
        ).encode()
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def planned_paths(
    plan: SyncPlan,
    bq_table: str,
    partitioned: PartitionedTablePlan | None,
) -> list[str]:
    """Return ordinary or changed-partition Parquet paths for one table."""
    match partitioned:
        case PartitionedTablePlan():
            return list(partitioned.changed_paths.values())
        case None:
            return plan.paths.get(bq_table, [])


def requires_full_rebuild(
    partitioned: PartitionedTablePlan | None,
    live_exists: bool,
) -> bool:
    """Return whether one table needs complete shadow reconstruction."""
    ordinary_table = partitioned is None
    missing_live_table = partitioned is not None and not live_exists
    partition_rebuild = partitioned is not None and partitioned.full_rebuild
    return ordinary_table or missing_live_table or partition_rebuild


def affected_partitions(
    partitioned: PartitionedTablePlan,
) -> list[PhysicalPartition]:
    """Return changed and removed partitions excluded from the live-table copy."""
    changed_partitions = [
        partitioned.current_partitions[partition_id]
        for partition_id in partitioned.changed_paths
    ]
    removed_partitions = list(partitioned.removed_partitions.values())
    return [*changed_partitions, *removed_partitions]


def create_incremental_shadow(
    pg_conn: Connection,
    table: TableConfig,
    affected: list[PhysicalPartition],
) -> None:
    """Create a shadow table and retain rows outside affected ranges."""
    schema = table.resolved_schema
    shadow = f"{table.table_name}__next"
    predicates = [
        SQL("({column} >= {lower} AND {column} < {upper})").format(
            column=Identifier(partition.column),
            lower=Literal(partition.lower),
            upper=Literal(partition.upper),
        )
        for partition in affected
    ]

    pg_conn.execute(
        load_template(
            {
                "path": "pg/prepare_incremental_table",
                "mapping": {
                    "schema": Identifier(schema),
                    "table": Identifier(table.table_name),
                    "next_table": Identifier(shadow),
                    "affected_partitions": SQL(" OR ").join(predicates),
                },
            }
        ).encode()
    )

    pg_conn.commit()


def prepare_tables(
    pg_conn: Connection,
    duckdb_conn: DBConnection,
    config: SyncConfig,
    plan: SyncPlan,
    changed: set[str],
) -> list[TableConfig]:
    """Prepare empty shadow tables, secure them, then load planned Parquet.

    A table is only appended to the returned list once its Parquet load
    succeeds. If any table fails to load, this function raises before
    returning, so ``publish_prepared_tables`` never runs for that sync --
    not even for tables already prepared earlier in the same loop. Their
    shadow tables are simply left in place and are safely recreated
    (``CREATE OR REPLACE`` / ``DROP TABLE IF EXISTS``) on the next
    successful run.
    """
    duckdb_conn.execute(
        load_template(
            {
                "path": "duckdb/attach_postgres",
                "mapping": {"pg_dsn": Literal(settings.PG_DSN)},
            }
        )
    )

    prepared: list[TableConfig] = []

    for table in config.tables:
        if table.bq_table not in changed:
            continue

        partitioned = plan.partitioned_tables.get(table.bq_table)
        paths = planned_paths(plan, table.bq_table, partitioned)
        shadow_name = f"{table.table_name}__next"

        live_exists = False
        if partitioned is not None:
            live_exists = table_exists(
                pg_conn,
                table.resolved_schema,
                table.table_name,
            )

        full_rebuild = requires_full_rebuild(partitioned, live_exists)

        if full_rebuild:
            if not paths:
                message = f"Parquet paths missing from sync plan: {table.bq_table}"
                raise RuntimeError(message)
            duckdb_conn.execute(
                load_template(
                    {
                        "path": "duckdb/create_table_from_parquet",
                        "mapping": {
                            "schema": Identifier(table.resolved_schema),
                            "table": Identifier(shadow_name),
                            "gcs_path": Literal(paths[0]),
                        },
                    }
                )
            )
        elif partitioned is not None:
            create_incremental_shadow(
                pg_conn,
                table,
                affected_partitions(partitioned),
            )

        with pg_conn.transaction():
            bootstrap_table(
                pg_conn,
                {
                    "schema": table.resolved_schema,
                    "table_name": shadow_name,
                    "rls": table.rls,
                },
            )

        load_table(duckdb_conn, table.resolved_schema, shadow_name, paths)
        prepared.append(table)

    return prepared


def publish_prepared_tables(
    pg_conn: Connection,
    prepared: Sequence[TableConfig],
) -> None:
    """Atomically publish every prepared shadow table."""
    for table in prepared:
        with pg_conn.transaction():
            publish_table(pg_conn, table)


def reload_postgrest(pg_conn: Connection, config: SyncConfig) -> None:
    """Revoke anonymous access and request a schema reload."""
    for schema in configured_schemas(config):
        pg_conn.execute(
            load_template(
                {
                    "path": "pg/revoke_anon",
                    "mapping": {
                        "schema": Identifier(schema),
                        "anon_role": Identifier(settings.AUTH_ANON_ROLE),
                    },
                }
            ).encode()
        )

    pg_conn.execute(b"NOTIFY pgrst, 'reload schema'")


def apply_sync_plan(
    pg_conn: Connection,
    duckdb_conn: DBConnection,
    config: SyncConfig,
    plan: SyncPlan,
) -> None:
    """Apply one sync plan using caller-provided connections."""
    changed = validate_sync_plan(config, plan)
    logger.info("Validated sync plan with {} changed tables", len(changed))

    initialize_schemas(pg_conn, config)
    logger.info("Initialized database schemas")

    prepared = prepare_tables(pg_conn, duckdb_conn, config, plan, changed)
    logger.info("Prepared {} tables", len(prepared))

    publish_prepared_tables(pg_conn, prepared)
    logger.info("Published {} changed tables", len(prepared))

    reload_postgrest(pg_conn, config)
    logger.info("Requested PostgREST schema reload")
