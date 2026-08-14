"""Parquet-to-PostgreSQL loading and atomic publication operations."""

from typing import TypedDict

from loguru import logger
from psycopg import Connection, sql

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
    statements = [
        load_template(
            {
                "path": "pg/grant_select",
                "mapping": {
                    "schema": sql.Identifier(schema),
                    "table": sql.Identifier(table_name),
                    "user_role": sql.Identifier(settings.AUTH_USER_ROLE),
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
                        "schema": sql.Identifier(schema),
                        "table": sql.Identifier(table_name),
                        "column": sql.Identifier(rls.column),
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
                    "path": "duckdb/load_table",
                    "mapping": {
                        "schema": sql.Identifier(schema),
                        "table_name": sql.Identifier(table_name),
                        "gcs_path": sql.Literal(path),
                    },
                }
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
                {
                    "path": "pg/create_index",
                    "mapping": {
                        "name": sql.Identifier(index.name),
                        "schema": sql.Identifier(table.resolved_schema),
                        "table": sql.Identifier(table_name),
                        "columns": sql.SQL(", ").join(
                            sql.Identifier(column) for column in index.columns
                        ),
                    },
                }
            ).encode()
        )


def publish_table(
    conn: Connection,
    table: DumpTable | WindowTable,
) -> None:
    """Atomically swap one prepared shadow table into service."""
    table_name = table.table_name
    shadow_name = f"{table_name}__next"

    conn.execute(
        load_template(
            {
                "path": "pg/swap_table",
                "mapping": {
                    "schema": sql.Identifier(table.resolved_schema),
                    "table": sql.Identifier(table_name),
                    "next_table": sql.Identifier(shadow_name),
                    "old_table": sql.Identifier(f"{table_name}__old"),
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
            {
                "path": "pg/init_roles",
                "mapping": {
                    "user_role": sql.Identifier(settings.AUTH_USER_ROLE),
                    "authenticator_role": sql.Identifier(
                        settings.AUTH_AUTHENTICATOR_ROLE
                    ),
                    "rls_schema": sql.Identifier("rls"),
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
                        "schema": sql.Identifier(schema),
                        "user_role": sql.Identifier(settings.AUTH_USER_ROLE),
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
) -> list[DumpTable | WindowTable]:
    """Prepare empty shadow tables, secure them, then load planned Parquet."""
    duckdb_conn.execute(
        load_template(
            {
                "path": "duckdb/attach_postgres",
                "mapping": {"pg_dsn": sql.Literal(settings.PG_DSN)},
            }
        )
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
                {
                    "path": "duckdb/create_table",
                    "mapping": {
                        "schema": sql.Identifier(table.resolved_schema),
                        "table": sql.Identifier(shadow_name),
                        "gcs_path": sql.Literal(paths[0]),
                    },
                }
            )
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
                {
                    "path": "pg/revoke_anon",
                    "mapping": {
                        "schema": sql.Identifier(schema),
                        "anon_role": sql.Identifier(settings.AUTH_ANON_ROLE),
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
