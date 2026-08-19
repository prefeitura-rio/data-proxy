"""FastStream producer application for scheduled synchronization runs."""

from datetime import UTC, datetime

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from loguru import logger

from ..constants import STREAM_TTL_SECONDS, SYNC_FINALIZE_STREAM, SYNC_TASKS_STREAM
from ..duckdb import connect
from ..logging import configure_logging
from ..models import FinalizeMessage, SyncConfig
from ..planning import build_sync_plan
from ..settings import settings
from ..state import has_active_run, save_sync_plan, trim_stale_entries

broker = RedisBroker(str(settings.REDIS_URL))
producer = FastStream(broker)


@producer.after_startup
async def publish_tasks() -> None:
    """Plan and publish one finite synchronization run."""
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())
    sync_id = datetime.now(UTC).isoformat()
    logger.info("Starting sync sync_id={}", sync_id)

    async with settings.make_redis() as redis:
        await trim_stale_entries(redis, SYNC_TASKS_STREAM, STREAM_TTL_SECONDS)
        await trim_stale_entries(redis, SYNC_FINALIZE_STREAM, STREAM_TTL_SECONDS)

        if await has_active_run(redis):
            logger.critical(
                "Previous sync run is still incomplete — refusing to start sync_id={}",
                sync_id,
            )
            producer.exit()
            return

        with connect() as db:
            plan, tasks = await build_sync_plan(
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

    if tasks:
        for task in tasks:
            await broker.publish(task, stream=SYNC_TASKS_STREAM)

        logger.info("Published {:d} tasks for sync_id={}", len(tasks), sync_id)
    else:
        await broker.publish(
            FinalizeMessage(sync_id=plan.sync_id),
            stream=SYNC_FINALIZE_STREAM,
        )

        logger.info("No tasks, going to final stage")

    producer.exit()


if __name__ == "__main__":
    configure_logging()
    uvloop.run(producer.run())
