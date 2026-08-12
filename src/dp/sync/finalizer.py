"""Sync finalizer: bootstraps tables, then loads GCS Parquet into Postgres."""

from typing import TypedDict
from uuid import uuid4

import psycopg
import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker, StreamSub
from loguru import logger
from redis.exceptions import ResponseError

from ..constants import FINALIZERS_GROUP, SYNC_FINALIZE_STREAM, SYNC_SHUTDOWN_CHANNEL
from ..duckdb import DBConnection, connect
from ..settings import settings
from ..templates import load_template
from .models import (
    DumpTable,
    FinalizeMessage,
    RlsConfig,
    ShutdownMessage,
    SyncConfig,
    WindowTable,
)

broker = RedisBroker(str(settings.REDIS_URL))
finalizer = FastStream(broker)

CONSUMER = str(uuid4())


class BootstrapInput(TypedDict):
    schema: str
    table_name: str
    rls: RlsConfig | None


def bootstrap_table(
    pg_conn: psycopg.Connection,
    params: BootstrapInput,
) -> None:
    """Create the table from Parquet DDL, grant access, and optionally enable RLS."""
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

    if rls is not None:
        sql.append(
            load_template(
                "pg/enable_rls",
                {"schema": schema, "table": table_name, "column": rls.column},
            )
        )
        logger.info("RLS enabled on {}.{} (column={})", schema, table_name, rls.column)

    pg_conn.execute(";".join(sql).encode())
    logger.info("Bootstrapped {}.{}", schema, table_name)


def list_parquets(duckdb_conn: DBConnection, table_name: str) -> list[str]:
    """Return all Parquet paths available for a configured table."""
    return [
        str(row[0])
        for row in duckdb_conn.execute(
            load_template(
                "duckdb/list_parquets",
                {"gcs_bucket": settings.GCS_BUCKET, "table_name": table_name},
            )
        ).fetchall()
    ]


def load_table(
    duckdb_conn: DBConnection,
    schema: str,
    table_name: str,
    paths: list[str],
) -> None:
    """Load all table Parquet files into a new shadow table."""
    for path in paths:
        duckdb_conn.execute(
            load_template(
                "duckdb/load_table",
                {"schema": schema, "table_name": table_name, "gcs_path": path},
            )
        )
        logger.info("Loaded {} into {}.{}", path, schema, table_name)


def create_indexes(
    pg_conn: psycopg.Connection,
    table: DumpTable | WindowTable,
    table_name: str,
) -> None:
    """Create configured indexes on a table."""
    for index in table.indexes:
        pg_conn.execute(
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
        logger.info(
            "Index {} ensured on {}.{}",
            index.name,
            table.resolved_schema,
            table_name,
        )


def publish_table(
    pg_conn: psycopg.Connection,
    table: DumpTable | WindowTable,
) -> None:
    """Atomically replace a prepared table and create its indexes."""
    table_name = table.table_name
    shadow_name = f"{table_name}__next"

    bootstrap_table(
        pg_conn,
        {
            "schema": table.resolved_schema,
            "table_name": shadow_name,
            "rls": table.rls,
        },
    )

    pg_conn.execute(
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

    create_indexes(pg_conn, table, table_name)
    logger.info("Published table: {}.{}", table.resolved_schema, table_name)


@finalizer.on_startup
async def ensure_consumer_group() -> None:
    async with settings.make_redis() as redis:
        try:
            await redis.xgroup_create(
                SYNC_FINALIZE_STREAM,
                FINALIZERS_GROUP,
                id="0",
                mkstream=True,
            )
            logger.debug("Consumer group {} created with id=0", FINALIZERS_GROUP)
        except ResponseError:
            logger.debug(
                "Consumer group {} already exists — skipping", FINALIZERS_GROUP
            )


@broker.subscriber(
    stream=StreamSub(SYNC_FINALIZE_STREAM, group=FINALIZERS_GROUP, consumer=CONSUMER)
)
async def finalize_sync(msg: FinalizeMessage) -> None:
    """Bootstrap tables and load GCS Parquet into Postgres via pg_duckdb."""
    logger.info("Finalizing sync_id={}", msg.sync_id)
    await broker.publish(ShutdownMessage(sync_id=msg.sync_id), SYNC_SHUTDOWN_CHANNEL)
    logger.info("Shutdown signal published for sync_id={}", msg.sync_id)
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())

    unique_schemas = {table.resolved_schema for table in config.tables}

    with (
        psycopg.connect(settings.PG_DSN) as pg_conn,
        connect() as duckdb_conn,
    ):
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
        for schema in unique_schemas:
            pg_conn.execute(
                load_template(
                    "pg/init_schema",
                    {"schema": schema, "user_role": settings.AUTH_USER_ROLE},
                ).encode()
            )
            logger.debug("Schema {} initialized", schema)

        pg_conn.commit()

        prepared_tables: list[DumpTable | WindowTable] = []
        duckdb_conn.execute(
            load_template("duckdb/attach_postgres", {"pg_dsn": settings.PG_DSN})
        )

        for table in config.tables:
            table_name = table.table_name
            paths = list_parquets(duckdb_conn, table_name)

            if not paths:
                logger.warning("No Parquet for {} — keeping current table", table_name)
                continue

            shadow_name = f"{table_name}__next"
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
            load_table(
                duckdb_conn,
                table.resolved_schema,
                shadow_name,
                paths,
            )
            prepared_tables.append(table)

        for table in prepared_tables:
            with pg_conn.transaction():
                publish_table(pg_conn, table)

        for schema in unique_schemas:
            pg_conn.execute(
                load_template(
                    "pg/revoke_anon",
                    {"schema": schema, "anon_role": settings.AUTH_ANON_ROLE},
                ).encode()
            )
            logger.info("Anonymous access revoked from schema {}", schema)

        pg_conn.execute(b"NOTIFY pgrst, 'reload schema'")
        logger.info("PostgREST schema reload requested")

    logger.info("Finalize complete for sync_id={}", msg.sync_id)
    finalizer.exit()


if __name__ == "__main__":
    uvloop.run(finalizer.run())
