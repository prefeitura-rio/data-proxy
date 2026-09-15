"""FastStream seeder application for shared database setup."""

from typing import cast

import uvloop
from faststream import FastStream, Logger
from faststream.exceptions import StopApplication
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, RedisStreamMessage
from redis.typing import StreamRangeResponse

from ..constants import PUBLISH_STREAM, SEED_STREAM, SEEDERS_GROUP
from ..errors import stop_on_error
from ..log import logger, runid
from ..metrics import metrics, tracker
from ..models import PublishTask, SeedTask, SyncPlan
from ..schema import initialize_schemas_for_plans
from ..settings import settings
from ..state import publication_exists, read_sync_plans
from ..state_machines import seeder_claim, worker_state
from ..utils import (
    ack_and_stop,
    remove_idle_consumers,
    stream_subscriptions,
)

broker = RedisBroker(
    str(settings.REDIS.write),
    logger=logger,
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)

seeder = FastStream(broker, logger=logger)

claim = seeder_claim

subs = stream_subscriptions(
    SEED_STREAM, SEEDERS_GROUP, settings.SEEDER_VISIBILITY_TIMEOUT_MS
)


async def dispatch_publication_tasks(task: SeedTask, plans: list[SyncPlan]) -> None:
    """Publish one publication task per schema plan to the publish stream."""
    stream_publisher = broker.publisher(stream=PUBLISH_STREAM)

    async with settings.redis() as redis, redis.pipeline(transaction=True) as pipe:
        for plan in plans:
            await stream_publisher.publish(
                PublishTask(run_id=task.run_id, schema_name=plan.schema_name),
                pipeline=pipe,
            )
        await pipe.execute()


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
@tracker("seeder")
async def seed_publication(
    task: SeedTask,
    logger: Logger,
    message: RedisStreamMessage,
) -> None:
    """Run idempotent setup, dispatch one publication task per schema, and stop."""
    if claim.is_claimed:
        logger.warning("Seeder already claimed a seed task, leaving the message")
        raise StopApplication

    claim.send("claim")

    runid.set(task.run_id)

    async with settings.redis() as redis:
        plans = await read_sync_plans(redis, task.run_id)
        entries = cast(StreamRangeResponse, await redis.xrange(PUBLISH_STREAM))

        if publication_exists(entries, task.run_id):
            logger.info("Seed skipped dispatch already exists")
            await ack_and_stop(message, redis, SEEDERS_GROUP)

    logger.info("Seed started plans=%d", len(plans))

    await initialize_schemas_for_plans(
        plans,
        settings.SCHEMA_WRITERS.dsn,
        settings.sync_config.schemas,
    )

    await dispatch_publication_tasks(task, plans)

    metrics.seed_runs_total.labels(status="success").inc()

    logger.info(
        "Seed completed schemas=%s", ",".join(plan.schema_name for plan in plans)
    )

    async with settings.redis() as redis:
        await ack_and_stop(message, redis, SEEDERS_GROUP)


@seeder.on_shutdown
async def cleanup_consumers() -> None:
    """Remove idle seeder consumers."""
    async with settings.redis() as redis:
        await remove_idle_consumers(redis, SEED_STREAM, SEEDERS_GROUP, subs)


if __name__ == "__main__":
    uvloop.run(seeder.run())
    raise SystemExit(worker_state.exit_code)
