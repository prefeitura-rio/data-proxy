"""FastStream finalizer application for atomic table publication."""

from time import monotonic
from uuid import uuid4

import psycopg
import uvloop
from asyncer import asyncify
from faststream import FastStream
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub
from loguru import logger

from ..constants import (
    FINALIZERS_GROUP,
    SYNC_FINALIZE_STREAM,
    SYNC_SHUTDOWN_CHANNEL,
)
from ..duckdb import connect
from ..errors import stop_on_error
from ..loading import apply_sync_plan
from ..log import configure_logging, elapsed_ms
from ..models import (
    FinalizeMessage,
    PublicationResult,
    ShutdownMessage,
    SyncConfig,
    SyncPlan,
)
from ..settings import settings
from ..state import (
    cleanup_consumer,
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

subs = {
    "new": StreamSub(
        SYNC_FINALIZE_STREAM,
        group=FINALIZERS_GROUP,
        consumer=str(uuid4()),
    ),
    "stale": StreamSub(
        SYNC_FINALIZE_STREAM,
        group=FINALIZERS_GROUP,
        consumer=str(uuid4()),
        min_idle_time=settings.FINALIZER_VISIBILITY_TIMEOUT_MS,
    ),
}


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


@broker.subscriber(stream=subs["new"])
async def finalize_new_sync(message: FinalizeMessage) -> None:
    """Finalize a newly delivered synchronization run."""
    await finalize_sync(message)


@broker.subscriber(stream=subs["stale"])
async def finalize_stale_sync(message: FinalizeMessage) -> None:
    """Finalize a synchronization run reclaimed after a crash."""
    await finalize_sync(message)


async def finalize_sync(message: FinalizeMessage) -> None:
    """Apply one required synchronization plan and commit its state."""
    log = logger.bind(
        component="finalizer",
        sync_id=message.sync_id,
    )

    started = monotonic()

    log.info("Finalization started")

    await broker.publish(
        ShutdownMessage(sync_id=message.sync_id),
        SYNC_SHUTDOWN_CHANNEL,
    )

    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())

    async with settings.make_redis() as redis:
        plan = await read_sync_plan(redis, message.sync_id)
        failed_paths = await read_failed_paths(redis, message.sync_id)
        log.info("Sync plan loaded", failed_path_count=len(failed_paths))

        result = await asyncify(apply_sync_plan_wrapper)(
            config,
            plan,
            failed_paths,
        )

        log.info(
            "Publication completed",
            published_table_count=len(result.published_tables),
        )

        await commit_sync_state(redis, result.plan, result.published_tables)
        log.info("Sync state committed", elapsed_ms=elapsed_ms(started))

    finalizer.exit()


@finalizer.on_shutdown
async def cleanup_finalizer_consumer() -> None:
    """Remove this finalizer consumer when it has no pending message."""
    async with settings.make_redis() as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(
                redis, SYNC_FINALIZE_STREAM, FINALIZERS_GROUP, sub.consumer
            )


if __name__ == "__main__":
    configure_logging()
    uvloop.run(finalizer.run())
