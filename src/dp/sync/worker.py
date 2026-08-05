"""Sync worker: consumes a SyncTask, BQ → GCS Parquet via DuckDB."""

from uuid import uuid4

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker, StreamSub
from loguru import logger

from ..constants import (
    SYNC_FINALIZE_STREAM,
    SYNC_JOB_KEY,
    SYNC_TASKS_STREAM,
    WORKERS_GROUP,
)
from ..duckdb import connect
from ..settings import settings
from ..templates import load_template
from .models import FinalizeMessage, SyncTask

broker = RedisBroker(str(settings.REDIS_URL))
worker = FastStream(broker)

CONSUMER = str(uuid4())


def build_mapping(msg: SyncTask) -> tuple[str, dict[str, str]]:
    """Return (template_name, mapping) for the given SyncTask."""
    mapping = {
        "bq_table": msg.bq_table,
        "gcs_path": msg.gcs_path,
    }

    if msg.partition_column and msg.partition_value:
        mapping["partition_column"] = msg.partition_column
        mapping["partition_value"] = msg.partition_value
        return "duckdb/write_window", mapping

    return "duckdb/write_dump", mapping


@broker.subscriber(
    stream=StreamSub(SYNC_TASKS_STREAM, group=WORKERS_GROUP, consumer=CONSUMER)
)
async def process_shard(msg: SyncTask) -> None:
    """Consume one task: BigQuery → GCS Parquet via DuckDB COPY."""
    template, mapping = build_mapping(msg)

    with connect() as db:
        db.execute(load_template(template, mapping))

    async with settings.make_redis() as redis:
        remaining = await redis.decr(SYNC_JOB_KEY.format(sync_id=msg.sync_id))

    if remaining == 0:
        logger.info("All shards done — publishing FinalizeMessage for {}", msg.sync_id)

        await broker.publish(
            FinalizeMessage(sync_id=msg.sync_id),
            stream=SYNC_FINALIZE_STREAM,
        )


if __name__ == "__main__":
    uvloop.run(worker.run())
