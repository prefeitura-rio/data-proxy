"""FastStream producer application for scheduled synchronization runs."""

from time import monotonic

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from loguru import logger
from redis.asyncio import Redis
from whenever import Instant

from ..constants import (
    FINALIZERS_GROUP,
    STREAM_TTL_SECONDS,
    SYNC_FINALIZE_STREAM,
    SYNC_TASKS_STREAM,
    WORKERS_GROUP,
)
from ..duckdb import connect
from ..log import configure_logging, elapsed_ms
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


async def recover_lost_finalization(redis: Redis) -> str | None:
    """Re-publish a lost finalizer message and return its sync ID."""
    sync_id = await read_active_sync_id(redis)

    if sync_id is None:
        return None

    if await read_remaining_tasks(redis, sync_id) > 0:
        return None

    if await has_pending_finalize_message(redis):
        return None

    await broker.publish(
        FinalizeMessage(sync_id=sync_id),
        stream=SYNC_FINALIZE_STREAM,
    )

    logger.info("Recovered lost finalizer message sync_id={}", sync_id)
    return sync_id


@producer.after_startup
async def publish_tasks() -> None:
    """Plan and publish one finite synchronization run."""
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())
    sync_id = Instant.now().format_iso()
    log = logger.bind(component="producer", sync_id=sync_id)
    started = monotonic()
    log.info("Sync started")

    async with settings.make_redis() as redis:
        await trim_stale_entries(redis, SYNC_TASKS_STREAM, STREAM_TTL_SECONDS)
        await trim_stale_entries(redis, SYNC_FINALIZE_STREAM, STREAM_TTL_SECONDS)

        await create_consumer_group(redis, SYNC_TASKS_STREAM, WORKERS_GROUP)
        await create_consumer_group(redis, SYNC_FINALIZE_STREAM, FINALIZERS_GROUP)

        log.info("Consumer groups ready")

        recovered_sync_id = await recover_lost_finalization(redis)
        if recovered_sync_id is not None:
            log.info(
                "Recovered finalization",
                recovered_sync_id=recovered_sync_id,
                elapsed_ms=elapsed_ms(started),
            )
            producer.exit()
            return

        log.info("Planning started")

        with connect() as db:
            plan, tasks = await build_sync_plan(
                config,
                redis,
                sync_id,
                settings.GCS_BUCKET,
                db,
            )

            log.info(
                "Planning completed",
                task_count=len(tasks),
                changed_table_count=sum(
                    len(schema_plan.signatures) for schema_plan in plan.plans
                )
                if plan
                else 0,
            )

            if plan is None:
                log.info("No table changes", elapsed_ms=elapsed_ms(started))
                producer.exit()
                return

            if not await create_run(redis, plan, len(tasks)):
                log.critical(
                    "Previous sync run is still incomplete — refusing to start",
                    elapsed_ms=elapsed_ms(started),
                )
                producer.exit()
                return

    if tasks:
        log.info("Publishing tasks", task_count=len(tasks))

        for index, task in enumerate(tasks, start=1):
            await broker.publish(task, stream=SYNC_TASKS_STREAM)

            if index % 500 == 0 or index == len(tasks):
                log.info("Task publication progress", published=index, total=len(tasks))

        log.info("Sync completed", elapsed_ms=elapsed_ms(started))
    else:
        await broker.publish(
            FinalizeMessage(sync_id=plan.sync_id),
            stream=SYNC_FINALIZE_STREAM,
        )

        log.info("No tasks, going to final stage", elapsed_ms=elapsed_ms(started))

    producer.exit()


if __name__ == "__main__":
    configure_logging()
    uvloop.run(producer.run())
