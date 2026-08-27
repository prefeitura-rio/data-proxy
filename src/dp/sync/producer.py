"""FastStream producer application for scheduled synchronization runs."""

from datetime import UTC, datetime

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from loguru import logger
from redis.asyncio import Redis

from ..constants import (
    FINALIZERS_GROUP,
    STREAM_TTL_SECONDS,
    SYNC_FINALIZE_STREAM,
    SYNC_TASKS_STREAM,
    WORKERS_GROUP,
)
from ..duckdb import connect
from ..logging import configure_logging
from ..models import FinalizeMessage, SyncConfig
from ..planning import build_sync_plan
from ..settings import settings
from ..state import (
    create_consumer_group,
    create_run,
    has_pending_finalize_message,
    read_active_sync_id,
    read_remaining_tasks,
    trim_stale_entries,
)

broker = RedisBroker(str(settings.REDIS_URL))
producer = FastStream(broker)


async def recover_lost_finalization(redis: Redis) -> bool:
    """Re-publish a lost finalizer message for a fully extracted run."""
    sync_id = await read_active_sync_id(redis)

    if sync_id is None:
        return False

    if await read_remaining_tasks(redis, sync_id) > 0:
        return False

    if await has_pending_finalize_message(redis):
        return False

    await broker.publish(
        FinalizeMessage(sync_id=sync_id),
        stream=SYNC_FINALIZE_STREAM,
    )

    logger.info("Recovered lost finalizer message sync_id={}", sync_id)
    return True


@producer.after_startup
async def publish_tasks() -> None:
    """Plan and publish one finite synchronization run."""
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())
    sync_id = datetime.now(UTC).isoformat()
    logger.info("Starting sync sync_id={}", sync_id)

    async with settings.make_redis() as redis:
        await trim_stale_entries(redis, SYNC_TASKS_STREAM, STREAM_TTL_SECONDS)
        await trim_stale_entries(redis, SYNC_FINALIZE_STREAM, STREAM_TTL_SECONDS)

        await create_consumer_group(redis, SYNC_TASKS_STREAM, WORKERS_GROUP)
        await create_consumer_group(redis, SYNC_FINALIZE_STREAM, FINALIZERS_GROUP)

        if await recover_lost_finalization(redis):
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

            if not await create_run(redis, plan, len(tasks)):
                logger.critical(
                    "Previous sync run is still incomplete — refusing to start sync_id={}",
                    sync_id,
                )
                producer.exit()
                return

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
