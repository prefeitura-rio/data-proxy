"""FastStream seeder application for shared database setup."""

from typing import cast
from uuid import uuid4

import psycopg
import uvloop
from faststream import FastStream
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub
from redis.asyncio.client import Pipeline

from ..constants import PUBLISH_STREAM, SEED_STREAM, SEEDERS_GROUP
from ..errors import stop_on_error
from ..models import PublishTask, SeedTask, SyncConfig
from ..schema import initialize_schemas
from ..settings import settings
from ..state import cleanup_consumer, read_sync_plans

broker = RedisBroker(
    str(settings.REDIS_URL),
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)
seeder = FastStream(broker)


def dispatch_exists(entries: object, run_id: str) -> bool:
    """Return whether publication work for one run already exists."""
    return run_id.encode() in repr(entries).encode()


subs = {
    "new": StreamSub(SEED_STREAM, group=SEEDERS_GROUP, consumer=str(uuid4())),
    "stale": StreamSub(
        SEED_STREAM,
        group=SEEDERS_GROUP,
        consumer=str(uuid4()),
        min_idle_time=settings.SEEDER_VISIBILITY_TIMEOUT_MS,
    ),
}


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
async def seed_sync(task: SeedTask) -> None:
    """Run idempotent setup and dispatch one publication task per schema."""
    async with settings.make_redis() as redis:
        plans = await read_sync_plans(redis, task.run_id)
        if dispatch_exists(await redis.xrange(PUBLISH_STREAM), task.run_id):
            seeder.exit()
            return
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())
    writers = settings.schema_writers()
    by_dsn: dict[str, list[str]] = {}
    for plan in plans:
        by_dsn.setdefault(writers.dsn(plan.schema_name), []).append(plan.schema_name)
    for dsn, schemas in by_dsn.items():
        with psycopg.connect(dsn) as conn:
            initialize_schemas(
                conn,
                SyncConfig(schemas={name: config.schemas[name] for name in schemas}),
            )
    stream_publisher = broker.publisher(stream=PUBLISH_STREAM)
    async with settings.make_redis() as redis, redis.pipeline(transaction=True) as pipe:
        for plan in plans:
            await stream_publisher.publish(
                PublishTask(run_id=task.run_id, schema_name=plan.schema_name),
                pipeline=cast(Pipeline, cast(object, pipe)),
            )
        await pipe.execute()
    seeder.exit()


@seeder.on_shutdown
async def cleanup_seeder_consumers() -> None:
    """Remove idle seeder consumers."""
    async with settings.make_redis() as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(redis, SEED_STREAM, SEEDERS_GROUP, sub.consumer)


if __name__ == "__main__":
    uvloop.run(seeder.run())
