"""FastStream publisher application for one schema plan."""

from time import monotonic
from uuid import uuid4

import uvloop
from asyncer import asyncify
from faststream import FastStream, Logger
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub

from ..constants import PUBLISH_STREAM, PUBLISHERS_GROUP
from ..errors import stop_on_error
from ..loading import publish_plan
from ..log import elapsed_ms, logger, runid, schemaname
from ..metrics import record_publication_metrics, tracker
from ..models import PublishTask, SyncConfig
from ..settings import settings
from ..state import (
    build_table_states,
    cleanup_consumer,
    read_failed_paths,
    read_sync_plan,
)
from ..utils import complete_publication, handle_missing_plan

broker = RedisBroker(
    str(settings.REDIS_URL),
    logger=logger,
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)

publisher = FastStream(broker, logger=logger)

subs = {
    "new": StreamSub(
        PUBLISH_STREAM,
        group=PUBLISHERS_GROUP,
        consumer=str(uuid4()),
        max_records=1,
        polling_interval=30,
    ),
    "stale": StreamSub(
        PUBLISH_STREAM,
        group=PUBLISHERS_GROUP,
        consumer=str(uuid4()),
        max_records=1,
        polling_interval=30,
        min_idle_time=settings.PUBLISHER_VISIBILITY_TIMEOUT_MS,
    ),
}


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
@tracker("publisher")
async def publish_schema(task: PublishTask, logger: Logger) -> None:
    """Publish one schema and complete its immutable plan field"""
    runid.set(task.run_id)
    schemaname.set(task.schema_name)

    async with settings.redis as redis:
        plan = await read_sync_plan(redis, task.run_id, task.schema_name)

        if plan is None:
            await handle_missing_plan(redis, task)
            return

        failed_paths = await read_failed_paths(redis, task.run_id)

    schema_config = SyncConfig(
        schemas={task.schema_name: settings.sync_config.schemas[task.schema_name]}
    )

    logger.info("Publish started")

    started = monotonic()

    result = await asyncify(publish_plan)(
        settings.schema_writers.dsn(task.schema_name),
        schema_config,
        plan,
        failed_paths,
    )

    duration = monotonic() - started

    record_publication_metrics(result, task.schema_name, duration)

    logger.info(
        "Publish completed tables=%d elapsed_ms=%d",
        len(result.published_tables),
        elapsed_ms(started),
    )

    states = build_table_states(result, schema_config)

    async with settings.redis as redis:
        await complete_publication(redis, task, states)


@publisher.on_shutdown
async def cleanup_consumers() -> None:
    """Remove idle publisher consumers"""
    async with settings.redis as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(
                redis, PUBLISH_STREAM, PUBLISHERS_GROUP, sub.consumer
            )


if __name__ == "__main__":
    uvloop.run(publisher.run())
