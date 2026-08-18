"""Valkey state operations for synchronization orchestration."""

import contextlib

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from .constants import (
    SYNC_JOB_KEY,
    SYNC_JOB_TTL_SECONDS,
    SYNC_PARTITIONS_KEY,
    SYNC_PLAN_KEY,
    SYNC_PLAN_TTL_SECONDS,
    SYNC_STATE_KEY,
    SYNC_TASKS_STREAM,
    WORKERS_GROUP,
)
from .models import PartitionManifest, SyncPlan


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


async def save_sync_plan(redis: Redis, plan: SyncPlan, task_count: int) -> None:
    """Persist a required sync plan, task counter, and worker group."""
    await redis.set(
        SYNC_PLAN_KEY.format(sync_id=plan.sync_id),
        plan.model_dump_json(),
        ex=SYNC_PLAN_TTL_SECONDS,
    )

    if task_count:
        await redis.set(
            SYNC_JOB_KEY.format(sync_id=plan.sync_id),
            task_count,
            ex=SYNC_JOB_TTL_SECONDS,
        )
        await create_consumer_group(redis, SYNC_TASKS_STREAM, WORKERS_GROUP)


async def read_sync_plan(redis: Redis, sync_id: str) -> SyncPlan:
    """Read a required finalizer plan or fail the synchronization."""
    raw = decode_redis_value(await redis.get(SYNC_PLAN_KEY.format(sync_id=sync_id)))
    if raw is None:
        message = f"Sync plan not found: {sync_id}"
        raise RuntimeError(message)
    return SyncPlan.model_validate_json(raw)


async def commit_sync_state(redis: Redis, plan: SyncPlan) -> None:
    """Commit all table signatures after successful publication."""
    for bq_table, signature in plan.signatures.items():
        await redis.set(state_key(bq_table), signature)

    for bq_table, table_plan in plan.partitioned_tables.items():
        manifest = PartitionManifest(
            table_signature=table_plan.table_signature,
            partitions=table_plan.current_partitions,
        )
        await redis.set(
            SYNC_PARTITIONS_KEY.format(bq_table=bq_table),
            manifest.model_dump_json(),
        )


async def complete_task(redis: Redis, sync_id: str) -> tuple[int, int]:
    """Decrement a task counter and return remaining tasks and stream lag."""
    remaining = await redis.decr(SYNC_JOB_KEY.format(sync_id=sync_id))
    groups = await redis.xinfo_groups(SYNC_TASKS_STREAM)
    lag = next(
        (group["lag"] for group in groups if group["name"] == WORKERS_GROUP.encode()),
        0,
    )

    return remaining, lag
