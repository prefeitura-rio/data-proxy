"""FastStream publisher application for one schema plan."""

from time import monotonic

import uvloop
from faststream import FastStream, Logger
from faststream.exceptions import StopApplication
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, RedisStreamMessage
from psycopg import AsyncConnection

from ..cache import clear_response_cache
from ..constants import PUBLISH_STREAM, PUBLISHERS_GROUP
from ..errors import stop_on_error
from ..kubernetes import refresh_postgrest
from ..loading import apply_sync_plan
from ..log import elapsed_ms, logger, runid, schemaname
from ..metrics import record_publication_metrics, tracker
from ..models import PublishTask, SyncConfig, SyncPlan
from ..replication import current_wal_lsn, wait_for_replica_replay
from ..settings import settings
from ..state import (
    build_table_states,
    read_failed_paths,
    read_sync_plan,
)
from ..state_machines import publisher_claim, worker_state
from ..utils import (
    ack_and_stop,
    complete_publication,
    handle_missing_plan,
    stream_subscriptions,
)
from ..utils import (
    remove_idle_consumers as remove_idle_clients,
)

broker = RedisBroker(
    str(settings.REDIS.write),
    logger=logger,
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)

publisher = FastStream(broker, logger=logger)

claim_state = publisher_claim

subscriptions = stream_subscriptions(
    PUBLISH_STREAM, PUBLISHERS_GROUP, settings.PUBLISHER_VISIBILITY_TIMEOUT_MS
)


async def publish_schema_task(
    task: PublishTask,
    logger: Logger,
    message: RedisStreamMessage,
    plan: SyncPlan,
    failed_paths: set[str],
) -> None:
    """Publish one schema plan, commit its state, flush the cache, and stop."""
    schema_config = SyncConfig(
        schemas={task.schema_name: settings.sync_config.schemas[task.schema_name]}
    )

    logger.info("Publish started")

    started = monotonic()

    pg_conn = await AsyncConnection.connect(
        settings.SCHEMA_WRITERS.dsn(task.schema_name)
    )

    result = await apply_sync_plan(
        pg_conn,
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

    target_lsn = await current_wal_lsn(pg_conn)
    await wait_for_replica_replay(pg_conn, target_lsn)

    await refresh_postgrest(task.schema_name, task.run_id)
    logger.info("Refreshed PostgREST-ro schema cache")

    async with settings.redis() as redis:
        await complete_publication(redis, task, states, pg_conn)

    await clear_response_cache(settings.FALLBACK_CACHE_REDIS_DB)
    logger.info("Flushed response cache")

    async with settings.redis() as redis:
        await ack_and_stop(message, redis, PUBLISHERS_GROUP)


@broker.subscriber(stream=subscriptions["new"])
@broker.subscriber(stream=subscriptions["stale"])
@tracker("publisher")
async def handle_publish_task(
    task: PublishTask,
    logger: Logger,
    message: RedisStreamMessage,
) -> None:
    """Publish one schema in this pod, then stop the application."""
    if claim_state.is_claimed:
        logger.warning("Publisher already claimed a schema, leaving the message")
        raise StopApplication

    claim_state.send("claim")

    runid.set(task.run_id)
    schemaname.set(task.schema_name)

    async with settings.redis() as redis:
        plan = await read_sync_plan(redis, task.run_id, task.schema_name)

        if not plan:
            async with await AsyncConnection.connect(settings.PG_DSN) as pg_conn:
                await handle_missing_plan(redis, task, pg_conn)
            await ack_and_stop(message, redis, PUBLISHERS_GROUP)

        failed_paths = await read_failed_paths(redis, task.run_id)

    await publish_schema_task(task, logger, message, plan, failed_paths)


@publisher.on_shutdown
async def remove_idle_consumers() -> None:
    """Remove idle publisher consumers."""
    async with settings.redis() as redis:
        await remove_idle_clients(
            redis, PUBLISH_STREAM, PUBLISHERS_GROUP, subscriptions
        )


if __name__ == "__main__":
    uvloop.run(publisher.run())
    raise SystemExit(worker_state.exit_code)
