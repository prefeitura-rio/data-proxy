"""Cross-domain worker coordination helpers."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import NoReturn
from uuid import uuid4

from faststream.exceptions import StopApplication
from faststream.redis import RedisStreamMessage, StreamSub
from psycopg import AsyncConnection
from redis.asyncio import Redis

from .log import logger
from .models import PublishTask, TableState
from .s3 import clear_s3_bucket
from .schema import reload_postgrest
from .settings import settings
from .state import cleanup_consumer, cleanup_run, complete_schema, read_active_run


@asynccontextmanager
async def atomic(pg_conn: AsyncConnection) -> AsyncGenerator[None]:
    """Commit on success and roll back on exception without a savepoint."""
    try:
        yield
    except Exception:
        await pg_conn.rollback()
        raise
    await pg_conn.commit()


def stream_subscriptions(
    stream: str,
    group: str,
    visibility_timeout_ms: int,
) -> dict[str, StreamSub]:
    """Return the new and stale subscriptions for one worker stream."""
    return {
        "new": StreamSub(
            stream,
            group=group,
            consumer=str(uuid4()),
            max_records=1,
            polling_interval=30,
        ),
        "stale": StreamSub(
            stream,
            group=group,
            consumer=str(uuid4()),
            max_records=1,
            polling_interval=30,
            min_idle_time=visibility_timeout_ms,
        ),
    }


async def remove_idle_consumers(
    redis: Redis,
    stream: str,
    group: str,
    subs: Mapping[str, StreamSub],
) -> None:
    """Remove every idle consumer of one stream when the pod shuts down."""
    for sub in subs.values():
        if sub.consumer is None:
            continue
        await cleanup_consumer(redis, stream, group, sub.consumer)


async def ack_and_stop(
    message: RedisStreamMessage,
    redis: Redis,
    group: str,
) -> NoReturn:
    """Acknowledge one message with its consumer group and stop the application."""
    await message.ack(redis=redis, group=group)
    raise StopApplication


async def handle_missing_plan(
    redis: Redis, task: PublishTask, pg_conn: AsyncConnection
) -> None:
    """Clean an empty active run after its publish plan disappears."""
    if (
        await read_active_run(redis) == task.run_id
        and await redis.hlen(f"dp:plans:{task.run_id}") == 0
    ):
        await reload_postgrest(pg_conn, settings.sync_config)

        await cleanup_run(redis, task.run_id)


async def complete_publication(
    redis: Redis,
    task: PublishTask,
    states: dict[str, TableState],
    pg_conn: AsyncConnection,
) -> None:
    """Persist schema state, clean the completed run, and empty the bucket."""
    remaining = await complete_schema(redis, task.run_id, task.schema_name, states)

    if remaining == 0:
        await reload_postgrest(pg_conn, settings.sync_config)

        await cleanup_run(redis, task.run_id)
        await clear_s3_bucket()
        logger.info("Bucket emptied")
