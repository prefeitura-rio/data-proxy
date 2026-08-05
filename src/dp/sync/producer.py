"""Sync producer: reads config, publishes SyncTasks to Redis, sets counter."""

from collections.abc import Iterator
from datetime import UTC, datetime

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from loguru import logger

from ..constants import SYNC_JOB_KEY, SYNC_TASKS_STREAM
from ..duckdb import DBConnection, connect
from ..settings import settings
from ..templates import load_template
from .models import DumpTable, SyncConfig, SyncTask, WindowTable

broker = RedisBroker(str(settings.REDIS_URL))
producer = FastStream(broker)


def discover_partitions(db: DBConnection, table: WindowTable) -> list[str]:
    """Query BigQuery for the last *n* distinct partition values."""
    sql = load_template(
        "duckdb/discover_partitions",
        {
            "bq_table": table.bq_table,
            "partition_column": table.partition.column,
            "n": str(table.partition.n),
        },
    )

    partitions = [str(row[0]) for row in db.execute(sql).fetchall()]
    partitions.sort(reverse=True)
    return partitions


def expand_config(
    config: SyncConfig,
    gcs_bucket: str,
    sync_id: str,
) -> Iterator[SyncTask]:
    """Expand a SyncConfig into individual SyncTasks.

    - ``dump`` tables produce exactly one task.
    - ``window`` tables query BigQuery for the last *n* partition values and
      produce one task per value.
    """
    with connect() as db:
        for table in config.tables:
            match table:
                case DumpTable():
                    yield table.to_task(sync_id, gcs_bucket)
                case WindowTable():
                    for partition in discover_partitions(db, table):
                        yield table.to_task(
                            sync_id,
                            gcs_bucket,
                            partition,
                            table.partition.column,
                        )


@producer.after_startup
async def publish_tasks() -> None:
    """Read the sync config, expand it into tasks, and publish them all."""
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())
    sync_id = datetime.now(UTC).isoformat()

    tasks = list(expand_config(config, settings.GCS_BUCKET, sync_id))
    key = SYNC_JOB_KEY.format(sync_id=sync_id)

    async with settings.make_redis() as redis:
        await redis.set(key, len(tasks), ex=3600)

    for msg in tasks:
        await broker.publish(msg, stream=SYNC_TASKS_STREAM)

    logger.info("Published {:d} tasks for sync_id={}", len(tasks), sync_id)
    producer.exit()


if __name__ == "__main__":
    uvloop.run(producer.run())
