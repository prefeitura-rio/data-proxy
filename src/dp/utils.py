"""Cross-domain worker coordination helpers."""

import psycopg
from redis.asyncio import Redis

from .models import PublishTask, TableState
from .schema import reload_postgrest
from .settings import settings
from .state import cleanup_run, complete_schema, read_active_run


async def handle_missing_plan(redis: Redis, task: PublishTask) -> None:
    """Clean an empty active run after its publish plan disappears"""
    if (
        await read_active_run(redis) == task.run_id
        and await redis.hlen(f"dp:plans:{task.run_id}") == 0
    ):
        with psycopg.connect(settings.PG_DSN) as conn:
            reload_postgrest(conn, settings.sync_config)
        await cleanup_run(redis, task.run_id)


async def complete_publication(
    redis: Redis, task: PublishTask, states: dict[str, TableState]
) -> None:
    """Persist schema state and clean the final completed run"""
    remaining = await complete_schema(redis, task.run_id, task.schema_name, states)
    if remaining == 0:
        with psycopg.connect(settings.PG_DSN) as conn:
            reload_postgrest(conn, settings.sync_config)
        await cleanup_run(redis, task.run_id)
