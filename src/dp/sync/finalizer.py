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
    Strategy,
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

    sql = [load_template("pg/grant_select", {"schema": schema, "table": table_name})]

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


def load_table(
    duckdb_conn: DBConnection,
    schema: str,
    table_name: str,
    strategy: Strategy,
    partition_column: str | None,
    target_table_name: str | None = None,
) -> None:
    """Load one table into Postgres using its configured strategy."""
    paths = [
        str(row[0])
        for row in duckdb_conn.execute(
            load_template(
                "duckdb/list_parquets",
                {"gcs_bucket": settings.GCS_BUCKET, "table_name": table_name},
            )
        ).fetchall()
    ]

    if not paths:
        logger.warning("No Parquet for {} — skipping", table_name)
        return

    target = target_table_name or table_name

    if strategy == Strategy.DUMP:
        duckdb_conn.execute(
            load_template(
                "duckdb/load_dump",
                {"schema": schema, "table_name": target, "gcs_path": paths[0]},
            )
        )
        logger.info("Loaded dump table: {}.{}", schema, target)
        return

    for path in paths:
        pv = path.rstrip("/").split("/")[-2]
        duckdb_conn.execute(
            load_template(
                "duckdb/delete_partition",
                {
                    "schema": schema,
                    "table_name": target,
                    "partition_column": partition_column or "",
                    "partition_value": pv,
                },
            )
        )
        duckdb_conn.execute(
            load_template(
                "duckdb/load_window",
                {
                    "schema": schema,
                    "table_name": target,
                    "gcs_path": path,
                    "partition_column": partition_column or "",
                    "partition_value": pv,
                },
            )
        )
        logger.info("Loaded partition {} for {}.{}", pv, schema, target)


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


def publish_dump(pg_conn: psycopg.Connection, table: DumpTable) -> None:
    """Atomically replace a dump table and create its indexes."""
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
    logger.info("Published dump table: {}.{}", table.resolved_schema, table_name)


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
    with psycopg.connect(settings.PG_DSN) as pg_conn:
        for schema in unique_schemas:
            pg_conn.execute(
                load_template("pg/init_schema", {"schema": schema}).encode()
            )
            logger.debug("Schema {} initialized", schema)

    with connect() as duckdb_conn:
        duckdb_conn.execute(
            load_template("duckdb/attach_postgres", {"pg_dsn": settings.PG_DSN})
        )

        for table in config.tables:
            match table:
                case DumpTable():
                    table_name = table.table_name
                    shadow_name = f"{table_name}__next"
                    gcs_path = f"s3://{settings.GCS_BUCKET}/{table_name}/data.parquet"

                    duckdb_conn.execute(
                        load_template(
                            "duckdb/create_table",
                            {
                                "schema": table.resolved_schema,
                                "table": shadow_name,
                                "gcs_path": gcs_path,
                            },
                        )
                    )

                    load_table(
                        duckdb_conn,
                        table.resolved_schema,
                        table_name,
                        Strategy.DUMP,
                        None,
                        target_table_name=shadow_name,
                    )
                case WindowTable(partition=partition):
                    table_name = table.table_name
                    gcs_paths = [
                        str(row[0])
                        for row in duckdb_conn.execute(
                            load_template(
                                "duckdb/list_parquets",
                                {
                                    "gcs_bucket": settings.GCS_BUCKET,
                                    "table_name": table_name,
                                },
                            )
                        ).fetchall()
                    ]

                    if gcs_paths:
                        duckdb_conn.execute(
                            load_template(
                                "duckdb/create_table",
                                {
                                    "schema": table.resolved_schema,
                                    "table": table_name,
                                    "gcs_path": gcs_paths[0],
                                },
                            )
                        )

                    load_table(
                        duckdb_conn,
                        table.resolved_schema,
                        table_name,
                        Strategy.WINDOW,
                        partition.column,
                    )

    for table in config.tables:
        with psycopg.connect(settings.PG_DSN) as pg_conn:
            if isinstance(table, DumpTable):
                publish_dump(pg_conn, table)
                continue

            bootstrap_table(
                pg_conn,
                {
                    "schema": table.resolved_schema,
                    "table_name": table.table_name,
                    "rls": table.rls,
                },
            )

            create_indexes(pg_conn, table, table.table_name)

    with psycopg.connect(settings.PG_DSN) as pg_conn:
        pg_conn.execute(b"NOTIFY pgrst, 'reload schema'")
        logger.info("PostgREST schema reload requested")

    logger.info("Finalize complete for sync_id={}", msg.sync_id)
    finalizer.exit()


if __name__ == "__main__":
    uvloop.run(finalizer.run())
