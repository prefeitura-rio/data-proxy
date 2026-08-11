"""Sync worker: consumes a SyncTask, BQ → GCS Parquet via DuckDB."""

from uuid import uuid4

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker, StreamSub
from loguru import logger

from ..constants import (
    SYNC_FINALIZE_STREAM,
    SYNC_JOB_KEY,
    SYNC_SHUTDOWN_CHANNEL,
    SYNC_TASKS_STREAM,
    WORKERS_GROUP,
)
from ..duckdb import connect
from ..settings import settings
from ..templates import load_template
from .models import FinalizeMessage, ShutdownMessage, SyncTask

broker = RedisBroker(str(settings.REDIS_URL))
worker = FastStream(broker)

CONSUMER = str(uuid4())


def build_columns(json_columns: list[str]) -> str:
    """Return a SELECT expression replacing STRUCT columns with to_json()."""
    if not json_columns:
        return "*"
    replacements = ", ".join(f'to_json("{col}") AS "{col}"' for col in json_columns)
    return f"* REPLACE ({replacements})"


def build_mapping(msg: SyncTask) -> tuple[str, dict[str, str]]:
    """Return (template_name, mapping) for the given SyncTask."""
    mapping = {
        "bq_table": msg.bq_table,
        "gcs_path":  msg.gcs_path,
        "columns":   build_columns(msg.json_columns),
    }

    if msg.partition_column and msg.partition_value:
        mapping["partition_column"] = msg.partition_column
        mapping["partition_value"]  = msg.partition_value
        return "duckdb/write_window", mapping

    return "duckdb/write_dump", mapping


@broker.subscriber(SYNC_SHUTDOWN_CHANNEL)
async def handle_shutdown(msg: ShutdownMessage) -> None:
    logger.info("Shutdown signal for sync_id={} — exiting", msg.sync_id)
    worker.exit()


@broker.subscriber(
    stream=StreamSub(
        SYNC_TASKS_STREAM,
        group=WORKERS_GROUP,
        consumer=CONSUMER,
        max_records=settings.WORKER_MAX_RECORDS,
    )
)
async def process_shard(msg: SyncTask) -> None:
    """Consume one task: BigQuery → GCS Parquet via DuckDB COPY."""
    logger.info("Processing {} (sync_id={})", msg.bq_table, msg.sync_id)
    template, mapping = build_mapping(msg)

    with connect() as db:
        db.execute(load_template(template, mapping))

    async with settings.make_redis() as redis:
        remaining = await redis.decr(SYNC_JOB_KEY.format(sync_id=msg.sync_id))
        groups = await redis.xinfo_groups(SYNC_TASKS_STREAM)

    lag = next((g["lag"] for g in groups if g["name"] == WORKERS_GROUP.encode()), 0)

    logger.debug(
        "Remaining tasks: {} lag: {} (sync_id={})", remaining, lag, msg.sync_id
    )

    if remaining == 0:
        logger.info("All shards done — publishing FinalizeMessage for {}", msg.sync_id)

        await broker.publish(
            FinalizeMessage(sync_id=msg.sync_id),
            stream=SYNC_FINALIZE_STREAM,
        )

    if lag == 0:
        logger.info("Queue empty — exiting")
        worker.exit()


if __name__ == "__main__":
    uvloop.run(worker.run())
