"""FastStream finalizer application for atomic table publication."""

from uuid import uuid4

import psycopg
import uvloop
from asyncer import asyncify
from faststream import FastStream
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub

from ..constants import (
    FINALIZERS_GROUP,
    SYNC_FINALIZE_STREAM,
    SYNC_SHUTDOWN_CHANNEL,
)
from ..duckdb import connect
from ..errors import stop_on_error
from ..loading import apply_sync_plan
from ..logging import configure_logging
from ..models import (
    FinalizeMessage,
    PublicationResult,
    ShutdownMessage,
    SyncConfig,
    SyncPlan,
)
from ..settings import settings
from ..state import (
    commit_sync_state,
    create_consumer_group,
    read_failed_paths,
    read_sync_plan,
)

broker = RedisBroker(
    str(settings.REDIS_URL),
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)
finalizer = FastStream(broker)

CONSUMER = str(uuid4())


@finalizer.on_startup
async def ensure_consumer_group() -> None:
    """Ensure the finalizer stream consumer group exists."""
    async with settings.make_redis() as redis:
        await create_consumer_group(redis, SYNC_FINALIZE_STREAM, FINALIZERS_GROUP)


def apply_sync_plan_wrapper(
    config: SyncConfig, plan: SyncPlan, failed_paths: set[str]
) -> PublicationResult:
    """Run the blocking Postgres/DuckDB publication for one plan."""
    with (
        psycopg.connect(settings.PG_DSN) as pg_conn,
        connect() as duckdb_conn,
    ):
        return apply_sync_plan(pg_conn, duckdb_conn, config, plan, failed_paths)


@broker.subscriber(
    stream=StreamSub(
        SYNC_FINALIZE_STREAM,
        group=FINALIZERS_GROUP,
        consumer=CONSUMER,
    )
)
async def finalize_sync(message: FinalizeMessage) -> None:
    """Apply one required synchronization plan and commit its state."""
    await broker.publish(
        ShutdownMessage(sync_id=message.sync_id),
        SYNC_SHUTDOWN_CHANNEL,
    )

    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())

    async with settings.make_redis() as redis:
        plan = await read_sync_plan(redis, message.sync_id)
        failed_paths = await read_failed_paths(redis, message.sync_id)

        result = await asyncify(apply_sync_plan_wrapper)(
            config,
            plan,
            failed_paths,
        )

        await commit_sync_state(redis, result.plan, result.published_tables)

    finalizer.exit()


if __name__ == "__main__":
    configure_logging()
    uvloop.run(finalizer.run())
