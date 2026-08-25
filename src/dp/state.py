"""Valkey state operations for synchronization orchestration."""

import contextlib
from datetime import UTC, datetime, timedelta

from loguru import logger
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError, ResponseError, WatchError
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from .constants import (
    SYNC_ACTIVE_KEY,
    SYNC_FAILURES_KEY,
    SYNC_JOB_KEY,
    SYNC_PARTITIONS_KEY,
    SYNC_PLAN_KEY,
    SYNC_RUN_TTL_SECONDS,
    SYNC_STATE_KEY,
    SYNC_TASK_RESULTS_KEY,
    SYNC_TRANSACTION_RETRIES,
)
from .models import (
    CompletionResult,
    PartitionManifest,
    SyncPlan,
    SyncTask,
    TaskFailure,
    TaskOutcome,
    TaskSuccess,
    task_outcome_adapter,
)


def decode_redis_value(value: bytes | str | None) -> str | None:
    """Decode a Valkey byte value while preserving strings and None."""
    match value:
        case bytes() as raw:
            return raw.decode()
        case _:
            return value


def state_key(bq_table: str) -> str:
    """Return the committed state key for one BigQuery table."""
    return SYNC_STATE_KEY.format(bq_table=bq_table)


async def read_table_signature(redis: Redis, bq_table: str) -> str | None:
    """Read one committed table signature."""
    return decode_redis_value(await redis.get(state_key(bq_table)))


async def read_partition_manifest(
    redis: Redis, bq_table: str
) -> PartitionManifest | None:
    """Read committed physical partition state for one source table."""
    raw = decode_redis_value(
        await redis.get(SYNC_PARTITIONS_KEY.format(bq_table=bq_table))
    )
    return PartitionManifest.model_validate_json(raw) if raw is not None else None


async def create_consumer_group(redis: Redis, stream: str, group: str) -> None:
    """Create one stream consumer group when it does not exist."""
    with contextlib.suppress(ResponseError):
        await redis.xgroup_create(stream, group, id="0", mkstream=True)


@retry(
    retry=retry_if_exception_type(WatchError),
    stop=stop_after_attempt(SYNC_TRANSACTION_RETRIES),
    reraise=True,
)
async def create_run(redis: Redis, plan: SyncPlan, task_count: int) -> bool:
    """Atomically create one run when no other run is active."""
    plan_key = SYNC_PLAN_KEY.format(sync_id=plan.sync_id)
    counter_key = SYNC_JOB_KEY.format(sync_id=plan.sync_id)

    async with redis.pipeline(transaction=True) as pipe:
        await pipe.watch(SYNC_ACTIVE_KEY)
        if await pipe.get(SYNC_ACTIVE_KEY) is not None:
            return False

        pipe.multi()
        pipe.set(SYNC_ACTIVE_KEY, plan.sync_id, ex=SYNC_RUN_TTL_SECONDS)
        pipe.set(plan_key, plan.model_dump_json(), ex=SYNC_RUN_TTL_SECONDS)
        pipe.set(counter_key, task_count, ex=SYNC_RUN_TTL_SECONDS)
        await pipe.execute()

    return True


async def read_sync_plan(redis: Redis, sync_id: str) -> SyncPlan:
    """Read a required finalizer plan or fail the synchronization."""
    raw = decode_redis_value(await redis.get(SYNC_PLAN_KEY.format(sync_id=sync_id)))
    if raw is None:
        message = f"Sync plan not found: {sync_id}"
        raise RuntimeError(message)
    return SyncPlan.model_validate_json(raw)


async def commit_sync_state(
    redis: Redis, plan: SyncPlan, published_tables: set[str]
) -> None:
    """Atomically commit successful state and clear temporary run state."""
    async with redis.pipeline(transaction=True) as pipe:
        for bq_table, signature in plan.signatures.items():
            if bq_table in published_tables:
                pipe.set(state_key(bq_table), signature)

        for bq_table, table_plan in plan.partitioned_tables.items():
            if bq_table in published_tables:
                manifest = PartitionManifest(
                    table_signature=table_plan.table_signature,
                    partitions=table_plan.current_partitions,
                )
                pipe.set(
                    SYNC_PARTITIONS_KEY.format(bq_table=bq_table),
                    manifest.model_dump_json(),
                )

        pipe.delete(
            SYNC_ACTIVE_KEY,
            SYNC_FAILURES_KEY.format(sync_id=plan.sync_id),
            SYNC_JOB_KEY.format(sync_id=plan.sync_id),
            SYNC_PLAN_KEY.format(sync_id=plan.sync_id),
            SYNC_TASK_RESULTS_KEY.format(sync_id=plan.sync_id),
        )
        await pipe.execute()


def failed_path(outcome: TaskOutcome) -> str | None:
    """Return the failed path from one typed task outcome."""
    match outcome:
        case TaskFailure(failed_path=path):
            return path
        case TaskSuccess():
            return None


async def read_failed_paths(redis: Redis, sync_id: str) -> set[str]:
    """Return failed paths from typed task outcomes and legacy workers."""
    results_key = SYNC_TASK_RESULTS_KEY.format(sync_id=sync_id)
    outcome_paths = [
        failed_path(task_outcome_adapter.validate_json(value))
        for value in await redis.hvals(results_key)
    ]

    legacy_failures = [
        decode_redis_value(value)
        for value in await redis.smembers(SYNC_FAILURES_KEY.format(sync_id=sync_id))
    ]

    return {path for path in [*outcome_paths, *legacy_failures] if path is not None}


async def read_completion_state(
    pipe: Pipeline,
    results_key: str,
    remaining_key: str,
    task: SyncTask,
) -> CompletionResult | int:
    """Return duplicate completion or current pending task state."""
    remaining_raw = await pipe.get(remaining_key)
    if remaining_raw is None:
        raise RuntimeError(f"Task counter not found: {task.sync_id}")

    remaining = int(remaining_raw)

    if await pipe.hexists(results_key, task.task_id):
        return CompletionResult(
            first_completion=False,
            remaining=remaining,
            should_finalize=False,
        )
    if remaining <= 0:
        raise RuntimeError(f"Invalid task counter: {task.sync_id}")

    return remaining


def queue_completion(
    pipe: Pipeline,
    results_key: str,
    remaining_key: str,
    task: SyncTask,
    outcome: TaskOutcome,
    next_remaining: int,
) -> None:
    """Queue one task result and remaining-count update."""
    pipe.multi()
    pipe.hset(results_key, task.task_id, outcome.model_dump_json())
    pipe.set(
        remaining_key,
        next_remaining,
        ex=SYNC_RUN_TTL_SECONDS,
    )
    pipe.expire(results_key, SYNC_RUN_TTL_SECONDS)


@retry(
    retry=retry_if_exception_type(WatchError),
    stop=stop_after_attempt(SYNC_TRANSACTION_RETRIES),
    reraise=True,
)
async def complete_task(
    redis: Redis,
    task: SyncTask,
    outcome: TaskOutcome,
) -> CompletionResult:
    """Perform one optimistic task-completion transaction."""
    results_key = SYNC_TASK_RESULTS_KEY.format(sync_id=task.sync_id)
    remaining_key = SYNC_JOB_KEY.format(sync_id=task.sync_id)

    async with redis.pipeline(transaction=True) as pipe:
        await pipe.watch(results_key, remaining_key)
        completion = await read_completion_state(pipe, results_key, remaining_key, task)

        match completion:
            case CompletionResult():
                return completion
            case int() as remaining:
                next_remaining = remaining - 1

        queue_completion(
            pipe,
            results_key,
            remaining_key,
            task,
            outcome,
            next_remaining,
        )
        await pipe.execute()

    return CompletionResult(
        first_completion=True,
        remaining=next_remaining,
        should_finalize=next_remaining == 0,
    )


async def has_active_run(redis: Redis) -> bool:
    """Return True when a previous synchronization run has not completed."""
    return await redis.get(SYNC_ACTIVE_KEY) is not None


async def cleanup_consumer(
    redis: Redis,
    stream: str,
    group: str,
    consumer: str,
) -> None:
    """Delete one consumer only when it owns no pending messages."""
    try:
        pending = await redis.xpending_range(
            stream,
            group,
            "-",
            "+",
            1,
            consumername=consumer,
        )

        if pending:
            return

        await redis.xgroup_delconsumer(stream, group, consumer)
    except RedisError as error:
        logger.warning(
            "Failed to clean worker consumer={} stream={} group={} error={}",
            consumer,
            stream,
            group,
            error,
        )


async def trim_stale_entries(redis: Redis, stream: str, ttl_seconds: int) -> None:
    """Drop stream entries older than a TTL, consumed or not."""
    cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
    minid = f"{int(cutoff.timestamp() * 1000)}-0"
    await redis.xtrim(stream, minid=minid, approximate=False)
