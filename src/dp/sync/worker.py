"""Sync worker: consumes a SyncTask, BQ → GCS Parquet via DuckDB."""

import logging
from uuid import uuid4

from faststream import FastStream
from faststream.redis import RedisBroker, StreamSub

from ..constants import SYNC_FINALIZE_STREAM, SYNC_TASKS_STREAM, WORKERS_GROUP
from ..duckdb import connect
from ..settings import settings
from ..templates import load_template
from .models import FinalizeMessage, SyncTask

logger = logging.getLogger(__name__)

broker = RedisBroker(str(settings.REDIS_URL))
worker = FastStream(broker)

CONSUMER = str(uuid4())


def build_mapping(msg: SyncTask) -> tuple[str, dict[str, str]]:
    """Build the template name and mapping dict from a SyncTask."""
    mapping = {"bq_table": msg.bq_table, "gcs_path": msg.gcs_path}

    if msg.partition_column and msg.partition_value:
        mapping["partition_column"] = msg.partition_column
        mapping["partition_value"] = msg.partition_value
        return "write_window", mapping

    return "write_dump", mapping


@broker.subscriber(
    stream=StreamSub(SYNC_TASKS_STREAM, group=WORKERS_GROUP, consumer=CONSUMER)
)
async def process_shard(msg: SyncTask) -> None:
    """Consume one task: BigQuery → GCS Parquet via DuckDB COPY."""
    template, mapping = build_mapping(msg)

    with connect(extensions=["bigquery"]) as db:
        db.execute(load_template(template, mapping))

    async with settings.make_redis() as redis:
        if await redis.xlen(SYNC_TASKS_STREAM) == 0:
            await broker.publish(
                FinalizeMessage(sync_id=msg.sync_id),
                stream=SYNC_FINALIZE_STREAM,
            )

    worker.exit()
