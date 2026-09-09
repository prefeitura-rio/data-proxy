"""FastStream seeder application for shared database setup."""

from typing import cast
from uuid import uuid4

import uvloop
from faststream import FastStream, Logger
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub
from redis.typing import StreamRangeResponse

from ..constants import PUBLISH_STREAM, SEED_STREAM, SEEDERS_GROUP
from ..errors import stop_on_error
from ..log import logger, runid
from ..metrics import metrics, tracker
from ..models import PublishTask, SeedTask
from ..schema import initialize_schemas_for_plans
from ..settings import settings
from ..state import cleanup_consumer, dispatch_exists, read_sync_plans

broker = RedisBroker(
    str(settings.REDIS_URL),
    logger=logger,
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)

seeder = FastStream(broker, logger=logger)

subs = {
    "new": StreamSub(
        SEED_STREAM,
        group=SEEDERS_GROUP,
        consumer=str(uuid4()),
        max_records=1,
        polling_interval=30,
    ),
    "stale": StreamSub(
        SEED_STREAM,
        group=SEEDERS_GROUP,
        consumer=str(uuid4()),
        max_records=1,
        polling_interval=30,
        min_idle_time=settings.SEEDER_VISIBILITY_TIMEOUT_MS,
    ),
}


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
@tracker("seeder")
async def seed_sync(task: SeedTask, logger: Logger) -> None:
    """Run idempotent setup and dispatch one publication task per schema"""
    runid.set(task.run_id)

    async with settings.redis as redis:
        plans = await read_sync_plans(redis, task.run_id)
        entries = cast(StreamRangeResponse, await redis.xrange(PUBLISH_STREAM))

        if dispatch_exists(entries, task.run_id):
            logger.info("Seed skipped dispatch already exists")
            return

    logger.info("Seed started plans=%d", len(plans))

    initialize_schemas_for_plans(
        plans,
        settings.schema_writers.dsn,
        settings.sync_config.schemas,
    )

    stream_publisher = broker.publisher(stream=PUBLISH_STREAM)

    async with settings.redis as redis, redis.pipeline(transaction=True) as pipe:
        for plan in plans:
            await stream_publisher.publish(
                PublishTask(run_id=task.run_id, schema_name=plan.schema_name),
                pipeline=pipe,
            )
        await pipe.execute()

    metrics.seed_runs_total.labels(status="success").inc()

    logger.info(
        "Seed completed schemas=%s", ",".join(plan.schema_name for plan in plans)
    )


@seeder.on_shutdown
async def cleanup_consumers() -> None:
    """Remove idle seeder consumers"""
    async with settings.redis as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(redis, SEED_STREAM, SEEDERS_GROUP, sub.consumer)


if __name__ == "__main__":
    uvloop.run(seeder.run())
