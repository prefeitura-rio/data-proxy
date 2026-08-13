"""FastStream producer application for scheduled synchronization runs."""

from datetime import UTC, datetime

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from loguru import logger

from ..constants import SYNC_TASKS_STREAM
from ..duckdb import connect
from ..models import SyncConfig
from ..planning import plan_sync
from ..settings import settings
from ..state import save_sync_plan

broker = RedisBroker(str(settings.REDIS_URL))
producer = FastStream(broker)


@producer.after_startup
async def publish_tasks() -> None:
    """Plan and publish one finite synchronization run."""
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())
    sync_id = datetime.now(UTC).isoformat()
    logger.info("Starting sync sync_id={}", sync_id)

    async with settings.make_redis() as redis:
        with connect() as db:
            plan, tasks = await plan_sync(
                config,
                redis,
                sync_id,
                settings.GCS_BUCKET,
                db,
            )

            if plan is None:
                logger.info("No table changes to publish for sync_id={}", sync_id)
                producer.exit()
                return

            await save_sync_plan(redis, plan, len(tasks))

    for task in tasks:
        await broker.publish(task, stream=SYNC_TASKS_STREAM)

    logger.info("Published {:d} tasks for sync_id={}", len(tasks), sync_id)
    producer.exit()


if __name__ == "__main__":
    uvloop.run(producer.run())
