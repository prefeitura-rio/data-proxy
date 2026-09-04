"""Valkey state operations for synchronization orchestration."""

import contextlib
from typing import cast

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import ResponseError, WatchError
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from .constants import (
    ACTIVE_KEY,
    DUMP_STREAM,
    DUMPERS_GROUP,
    PLANS_KEY,
    PUBLISH_STREAM,
    PUBLISHERS_GROUP,
    REMAINING_KEY,
    RESULTS_KEY,
    SEED_STREAM,
    SEEDERS_GROUP,
    STATE_KEY,
    SYNC_RUN_TTL_SECONDS,
    SYNC_TRANSACTION_RETRIES,
)
from .models import (
    DumpFailure,
    DumpResult,
    DumpTask,
    PartitionManifest,
    SyncPlan,
    TableState,
    task_outcome_adapter,
)


def decode_redis_value(value: bytes | str | None) -> str | None:
    """Decode a Valkey value while preserving strings and None."""
    return value.decode() if isinstance(value, bytes) else value


async def read_table_signature(redis: Redis, table: str) -> str | None:
    """Read the committed signature for one table."""
    state = await read_table_state(redis, table)
    return state.signature if state is not None else None


async def read_partition_manifest(redis: Redis, table: str) -> PartitionManifest | None:
    """Read a partition manifest from unified table state."""
    state = await read_table_state(redis, table)
    if state is None or state.partitions is None:
        return None
    return PartitionManifest(
        table_signature=state.signature, partitions=state.partitions
    )


async def read_table_state(redis: Redis, table: str) -> TableState | None:
    """Read committed state for one table."""
    raw = decode_redis_value(await redis.get(STATE_KEY.format(table=table)))
    return TableState.model_validate_json(raw) if raw is not None else None


async def create_consumer_group(redis: Redis, stream: str, group: str) -> None:
    """Create a stream consumer group when it does not exist."""
    with contextlib.suppress(ResponseError):
        await redis.xgroup_create(stream, group, id="0", mkstream=True)


@retry(
    retry=retry_if_exception_type(WatchError),
    stop=stop_after_attempt(SYNC_TRANSACTION_RETRIES),
    reraise=True,
)
async def create_run(
    redis: Redis, run_id: str, plans: list[SyncPlan], task_count: int
) -> bool:
    """Atomically reserve one run and store its schema plans."""
    plans_key = PLANS_KEY.format(run_id=run_id)
    remaining_key = REMAINING_KEY.format(run_id=run_id)
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.watch(ACTIVE_KEY)
        if await pipe.get(ACTIVE_KEY) is not None:
            return False
        pipe.multi()
        pipe.set(ACTIVE_KEY, run_id, ex=SYNC_RUN_TTL_SECONDS)
        for plan in plans:
            pipe.hset(plans_key, plan.schema_name, plan.model_dump_json())
        pipe.set(remaining_key, task_count, ex=SYNC_RUN_TTL_SECONDS)
        await pipe.execute()
    return True


async def read_sync_plan(
    redis: Redis, run_id: str, schema_name: str
) -> SyncPlan | None:
    """Read one immutable schema plan."""
    raw = decode_redis_value(
        await redis.hget(PLANS_KEY.format(run_id=run_id), schema_name)
    )
    return SyncPlan.model_validate_json(raw) if raw is not None else None


async def read_active_run(redis: Redis) -> str | None:
    """Read the active run ID."""
    return decode_redis_value(await redis.get(ACTIVE_KEY))


async def read_remaining(redis: Redis, run_id: str) -> int | None:
    """Read remaining dump tasks for one run."""
    raw = decode_redis_value(await redis.get(REMAINING_KEY.format(run_id=run_id)))
    return int(raw) if raw is not None else None


async def read_sync_plans(redis: Redis, run_id: str) -> list[SyncPlan]:
    """Read all immutable schema plans for one run."""
    return [
        SyncPlan.model_validate_json(value)
        for value in await redis.hvals(PLANS_KEY.format(run_id=run_id))
    ]


@retry(
    retry=retry_if_exception_type(WatchError),
    stop=stop_after_attempt(SYNC_TRANSACTION_RETRIES),
    reraise=True,
)
async def complete_dump(redis: Redis, task: DumpTask, result: DumpResult) -> int | None:
    """Store one unique dump result and return remaining tasks, or None if duplicate."""
    results_key = RESULTS_KEY.format(run_id=task.run_id)
    remaining_key = REMAINING_KEY.format(run_id=task.run_id)
    async with redis.pipeline(transaction=True) as raw_pipe:
        pipe: Pipeline = raw_pipe
        await pipe.watch(results_key, remaining_key)
        remaining_raw = cast(bytes | None, await pipe.get(remaining_key))
        if remaining_raw is None:
            raise RuntimeError(f"Remaining task count not found: {task.run_id}")
        if await pipe.hexists(results_key, task.task_id):
            return None
        remaining = int(decode_redis_value(remaining_raw) or 0)
        if remaining <= 0:
            raise RuntimeError(f"Invalid remaining task count: {task.run_id}")
        next_remaining = remaining - 1
        pipe.multi()
        pipe.hset(results_key, task.task_id, result.model_dump_json())
        pipe.set(remaining_key, next_remaining, ex=SYNC_RUN_TTL_SECONDS)
        pipe.expire(results_key, SYNC_RUN_TTL_SECONDS)
        pipe.expire(ACTIVE_KEY, SYNC_RUN_TTL_SECONDS)
        await pipe.execute()
    return next_remaining


async def read_failed_paths(redis: Redis, run_id: str) -> set[str]:
    """Return failed Parquet paths from dump results."""
    results = [
        task_outcome_adapter.validate_json(value)
        for value in await redis.hvals(RESULTS_KEY.format(run_id=run_id))
    ]
    return {result.failed_path for result in results if isinstance(result, DumpFailure)}


@retry(
    retry=retry_if_exception_type(WatchError),
    stop=stop_after_attempt(SYNC_TRANSACTION_RETRIES),
    reraise=True,
)
async def complete_schema(
    redis: Redis,
    run_id: str,
    schema_name: str,
    states: dict[str, TableState],
) -> int | None:
    """Commit one schema state and remove its immutable plan field."""
    plans_key = PLANS_KEY.format(run_id=run_id)
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.watch(plans_key)
        if not await pipe.hexists(plans_key, schema_name):
            return None
        pipe.multi()
        for table, state in states.items():
            pipe.set(STATE_KEY.format(table=table), state.model_dump_json())
        pipe.hdel(plans_key, schema_name)
        pipe.hlen(plans_key)
        result = await pipe.execute()
    return cast(int, result[-1])


async def cleanup_run(redis: Redis, run_id: str) -> None:
    """Delete temporary state after final PostgREST reload."""
    await redis.delete(
        ACTIVE_KEY,
        PLANS_KEY.format(run_id=run_id),
        REMAINING_KEY.format(run_id=run_id),
        RESULTS_KEY.format(run_id=run_id),
    )


async def cleanup_consumer(
    redis: Redis, stream: str, group: str, consumer: str
) -> None:
    """Delete one consumer when it has no pending messages."""
    if await redis.xpending_range(stream, group, "-", "+", 1, consumername=consumer):
        return
    with contextlib.suppress(ResponseError):
        await redis.xgroup_delconsumer(stream, group, consumer)


async def ensure_groups(redis: Redis) -> None:
    """Create all pipeline consumer groups."""
    await create_consumer_group(redis, DUMP_STREAM, DUMPERS_GROUP)
    await create_consumer_group(redis, SEED_STREAM, SEEDERS_GROUP)
    await create_consumer_group(redis, PUBLISH_STREAM, PUBLISHERS_GROUP)
