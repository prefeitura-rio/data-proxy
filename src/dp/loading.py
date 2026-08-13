"""Parquet-to-PostgreSQL loading and atomic publication operations."""

from typing import TypedDict

from loguru import logger
from psycopg import Connection

from .duckdb import DBConnection
from .models import DumpTable, RlsConfig, SyncConfig, SyncPlan, WindowTable
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
    sql = [
        load_template(
            "pg/grant_select",
            {
                "schema": schema,
                "table": table_name,
                "user_role": settings.AUTH_USER_ROLE,
            },
        )
    ]

    if rls:
        sql.append(
            load_template(
                "pg/enable_rls",
                {"schema": schema, "table": table_name, "column": rls.column},
            )
        )

    pg_conn.execute(";".join(sql).encode())


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
                "duckdb/load_table",
                {"schema": schema, "table_name": table_name, "gcs_path": path},
            )
        )


def create_indexes(
    conn: Connection,
    table: DumpTable | WindowTable,
    table_name: str,
) -> None:
    """Create every configured index on a table."""
    for index in table.indexes:
        conn.execute(
            load_template(
                "pg/create_index",
                {
                    "name": index.name,
                    "schema": table.resolved_schema,
                    "table": table_name,
                    "columns": ", ".join(index.columns),
                },
            ).encode()
        )


def publish_table(
    conn: Connection,
    table: DumpTable | WindowTable,
) -> None:
    """Atomically swap one prepared shadow table into service."""
    table_name = table.table_name
    shadow_name = f"{table_name}__next"

    bootstrap_table(
        conn,
        {
            "schema": table.resolved_schema,
            "table_name": shadow_name,
            "rls": table.rls,
        },
    )

    conn.execute(
        load_template(
            "pg/swap_table",
            {
                "schema": table.resolved_schema,
                "table": table_name,
                "next_table": shadow_name,
                "old_table": f"{table_name}__old",
            },
        ).encode()
    )

    create_indexes(conn, table, table_name)


def configured_schemas(config: SyncConfig) -> set[str]:
    """Return distinct PostgreSQL schemas used by a config."""
    return {table.resolved_schema for table in config.tables}


def validate_sync_plan(config: SyncConfig, plan: SyncPlan) -> set[str]:
    """Validate planned tables and return their configured names."""
    changed = set(plan.signatures)
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
            "pg/init_roles",
            {
                "user_role": settings.AUTH_USER_ROLE,
                "authenticator_role": settings.AUTH_AUTHENTICATOR_ROLE,
                "rls_schema": "rls",
            },
        ).encode()
    )
    for schema in configured_schemas(config):
        pg_conn.execute(
            load_template(
                "pg/init_schema",
                {"schema": schema, "user_role": settings.AUTH_USER_ROLE},
            ).encode()
        )
    pg_conn.commit()


def prepare_tables(
    duckdb_conn: DBConnection,
    config: SyncConfig,
    plan: SyncPlan,
    changed: set[str],
) -> list[DumpTable | WindowTable]:
    """Attach PostgreSQL and load planned Parquet into shadow tables."""
    duckdb_conn.execute(
        load_template("duckdb/attach_postgres", {"pg_dsn": settings.PG_DSN})
    )
    prepared: list[DumpTable | WindowTable] = []
    for table in config.tables:
        if table.bq_table not in changed:
            continue

        paths = plan.paths.get(table.bq_table)
        if not paths:
            message = f"Parquet paths missing from sync plan: {table.bq_table}"
            raise RuntimeError(message)

        shadow_name = f"{table.table_name}__next"
        duckdb_conn.execute(
            load_template(
                "duckdb/create_table",
                {
                    "schema": table.resolved_schema,
                    "table": shadow_name,
                    "gcs_path": paths[0],
                },
            )
        )
        load_table(duckdb_conn, table.resolved_schema, shadow_name, paths)
        prepared.append(table)
    return prepared


def publish_prepared_tables(
    pg_conn: Connection,
    prepared: list[DumpTable | WindowTable],
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
                "pg/revoke_anon",
                {"schema": schema, "anon_role": settings.AUTH_ANON_ROLE},
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

    prepared = prepare_tables(duckdb_conn, config, plan, changed)
    logger.info("Prepared {} tables", len(prepared))

    publish_prepared_tables(pg_conn, prepared)
    logger.info("Published {} changed tables", len(prepared))

    reload_postgrest(pg_conn, config)
    logger.info("Requested PostgREST schema reload")
