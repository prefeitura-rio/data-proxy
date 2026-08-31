"""FastStream publisher application for one schema plan."""

from typing import cast
from uuid import uuid4

import psycopg
import uvloop
from asyncer import asyncify
from faststream import FastStream
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub

from ..constants import PUBLISH_STREAM, PUBLISHERS_GROUP
from ..duckdb import connect
from ..errors import stop_on_error
from ..loading import apply_sync_plan
from ..models import PublishTask, SyncConfig, SyncPlan, TableState
from ..protocols import PostgresPublication
from ..schema import reload_postgrest
from ..settings import settings
from ..state import (
    cleanup_consumer,
    cleanup_run,
    complete_schema,
    read_failed_paths,
    read_sync_plan,
)

broker = RedisBroker(
    str(settings.REDIS_URL),
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)
publisher = FastStream(broker)
subs = {
    "new": StreamSub(PUBLISH_STREAM, group=PUBLISHERS_GROUP, consumer=str(uuid4())),
    "stale": StreamSub(
        PUBLISH_STREAM,
        group=PUBLISHERS_GROUP,
        consumer=str(uuid4()),
        min_idle_time=settings.PUBLISHER_VISIBILITY_TIMEOUT_MS,
    ),
}


def publish_plan(dsn: str, config: SyncConfig, plan: SyncPlan, failed_paths: set[str]):
    """Run blocking schema publication."""
    with psycopg.connect(dsn) as pg_conn, connect() as duckdb_conn:
        return apply_sync_plan(
            cast(PostgresPublication, cast(object, pg_conn)),
            duckdb_conn,
            config,
            plan,
            failed_paths,
        )


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
async def publish_schema(task: PublishTask) -> None:
    """Publish one schema and complete its immutable plan field."""
    async with settings.make_redis() as redis:
        plan = await read_sync_plan(redis, task.run_id, task.schema_name)
        if plan is None:
            if (
                await redis.get("dp:active") == task.run_id
                and await redis.hlen(f"dp:plans:{task.run_id}") == 0
            ):
                with psycopg.connect(settings.PG_DSN) as conn:
                    reload_postgrest(
                        conn,
                        SyncConfig.model_validate_json(
                            settings.SYNC_CONFIG_PATH.read_text()
                        ),
                    )
                await cleanup_run(redis, task.run_id)
            publisher.exit()
            return
        failed_paths = await read_failed_paths(redis, task.run_id)

    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())
    schema_config = SyncConfig(
        schemas={task.schema_name: config.schemas[task.schema_name]}
    )
    result = await asyncify(publish_plan)(
        settings.schema_writers().dsn(task.schema_name),
        schema_config,
        plan,
        failed_paths,
    )
    states: dict[str, TableState] = {}
    for table_name, signature in result.plan.signatures.items():
        if table_name in result.published_tables:
            table = next(
                table for table in schema_config.tables if table.name == table_name
            )
            states[table_name] = TableState(
                strategy=table.strategy,
                signature=signature,
                partitions=None,
            )
    for table_name, table_plan in result.plan.partitioned_tables.items():
        if table_name in result.published_tables:
            states[table_name] = TableState(
                strategy=next(
                    table.strategy
                    for table in schema_config.tables
                    if table.name == table_name
                ),
                signature=table_plan.table_signature,
                partitions=table_plan.current_partitions,
            )
    async with settings.make_redis() as redis:
        remaining = await complete_schema(redis, task.run_id, task.schema_name, states)
        if remaining == 0:
            with psycopg.connect(settings.PG_DSN) as conn:
                reload_postgrest(conn, config)
            await cleanup_run(redis, task.run_id)
    publisher.exit()


@publisher.on_shutdown
async def cleanup_publisher_consumers() -> None:
    """Remove idle publisher consumers."""
    async with settings.make_redis() as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(
                redis, PUBLISH_STREAM, PUBLISHERS_GROUP, sub.consumer
            )


if __name__ == "__main__":
    uvloop.run(publisher.run())
