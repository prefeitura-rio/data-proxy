"""Sync finalizer: loads GCS Parquet files into Postgres via DuckDB postgres_scanner."""

import logging
from uuid import uuid4

from faststream import FastStream
from faststream.redis import RedisBroker, StreamSub

from ..constants import FINALIZERS_GROUP, SYNC_FINALIZE_STREAM
from ..duckdb import DBConnection, connect
from ..settings import settings
from ..templates import load_template
from ..validators import str_list
from .models import DumpTable, FinalizeMessage, Strategy, SyncConfig, WindowTable

logger = logging.getLogger(__name__)

broker = RedisBroker(str(settings.REDIS_URL))
finalizer = FastStream(broker)

CONSUMER = str(uuid4())


def partition_value_from_path(path: str) -> str:
    """Extract the partition value from a GCS Parquet path.

    gs://bucket/table/2025-01-15/data.parquet  →  2025-01-15
    """
    return path.rstrip("/").split("/")[-2]


def gcs_paths_for_table(table_name: str) -> list[str]:
    """List all Parquet objects under gs://{GCS_BUCKET}/{table_name}/ via DuckDB glob()."""
    with connect() as db:
        sql = load_template(
            "list_parquets",
            {"gcs_bucket": settings.GCS_BUCKET, "table_name": table_name},
        )

        return str_list.validate_python([row[0] for row in db.execute(sql).fetchall()])


def load_table(
    db: DBConnection,
    table_name: str,
    strategy: Strategy,
    partition_column: str | None,
) -> None:
    """Load one table into Postgres using its configured strategy."""
    schema = settings.PG_SCHEMA
    paths = gcs_paths_for_table(table_name)

    if not paths:
        logger.warning("No Parquet for %s — skipping", table_name)
        return

    if strategy == Strategy.DUMP:
        sql = load_template(
            "load_dump",
            {
                "schema": schema,
                "table_name": table_name,
                "gcs_path": paths[0],
            },
        )

        db.execute(sql)
        logger.info("Loaded dump table: %s.%s", schema, table_name)
        return

    for path in paths:
        pv = partition_value_from_path(path)
        sql = load_template(
            "load_window",
            {
                "schema": schema,
                "table_name": table_name,
                "gcs_path": path,
                "partition_column": partition_column or "",
                "partition_value": pv,
            },
        )

        db.execute(sql)
        logger.info("Loaded partition %s for %s.%s", pv, schema, table_name)


@broker.subscriber(
    stream=StreamSub(SYNC_FINALIZE_STREAM, group=FINALIZERS_GROUP, consumer=CONSUMER)
)
async def finalize_sync(msg: FinalizeMessage) -> None:
    """Load all GCS Parquet files into Postgres via DuckDB postgres_scanner."""
    logger.info("Finalizing sync_id=%s", msg.sync_id)
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())

    with connect() as db:
        db.execute(f"ATTACH '{settings.PG_DSN}' AS pg (TYPE postgres)")

        for table in config.tables:
            match table:
                case DumpTable():
                    load_table(db, table.table_name, Strategy.DUMP, None)
                case WindowTable():
                    load_table(
                        db,
                        table.table_name,
                        Strategy.WINDOW,
                        table.partition.column,
                    )

    logger.info("Finalize complete for sync_id=%s", msg.sync_id)
    finalizer.exit()
