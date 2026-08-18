"""Tests for Valkey synchronization state operations."""

import pytest
from helpers import FakeRedis, FakeRedisGroup, redis_client
from redis.exceptions import ResponseError

from dp.constants import (
    SYNC_ACTIVE_KEY,
    SYNC_JOB_KEY,
    SYNC_JOB_TTL_SECONDS,
    SYNC_PARTITIONS_KEY,
    SYNC_PLAN_KEY,
    SYNC_PLAN_TTL_SECONDS,
    SYNC_STATE_KEY,
)
from dp.models import (
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    RangeSelection,
    SyncPlan,
)
from dp.state import (
    commit_sync_state,
    complete_task,
    create_consumer_group,
    decode_redis_value,
    has_active_run,
    read_partition_manifest,
    read_sync_plan,
    read_table_signature,
    save_sync_plan,
    trim_stale_entries,
)


def test_decodes_bytes_and_preserves_strings() -> None:
    """Valkey values are normalized to strings."""
    assert decode_redis_value(b"value") == "value"
    assert decode_redis_value("value") == "value"
    assert decode_redis_value(None) is None


@pytest.mark.asyncio
async def test_reads_table_signature() -> None:
    """Committed signatures are read from their table state key."""
    fake = FakeRedis()
    fake.store[SYNC_STATE_KEY.format(bq_table="p.d.t")] = "100"

    result = await read_table_signature(redis_client(fake), "p.d.t")

    assert result == "100"


@pytest.mark.asyncio
async def test_saves_and_reads_required_plan() -> None:
    """Plans are persisted with their counter and can be read back."""
    fake = FakeRedis()
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.d.t": "100"},
        paths={"p.d.t": ["s3://b/t/data.parquet"]},
    )

    await save_sync_plan(redis_client(fake), plan, 1)
    result = await read_sync_plan(redis_client(fake), "s1")

    assert result == plan
    assert SYNC_PLAN_KEY.format(sync_id="s1") in fake.store
    assert fake.store[SYNC_ACTIVE_KEY] == "s1"

    ttl_by_key = {key: ex for key, _, ex in fake.set_calls}
    assert ttl_by_key[SYNC_PLAN_KEY.format(sync_id="s1")] == SYNC_PLAN_TTL_SECONDS
    assert ttl_by_key[SYNC_JOB_KEY.format(sync_id="s1")] == SYNC_JOB_TTL_SECONDS


@pytest.mark.asyncio
async def test_zero_task_plan_has_no_worker_counter() -> None:
    """Deletion-only plans do not create an unusable zero-valued counter."""
    fake = FakeRedis()
    await save_sync_plan(redis_client(fake), SyncPlan(sync_id="s1"), 0)

    assert SYNC_JOB_KEY.format(sync_id="s1") not in fake.store
    assert fake.store[SYNC_ACTIVE_KEY] == "s1"


@pytest.mark.asyncio
async def test_missing_plan_fails() -> None:
    """A finalizer cannot infer work without its plan."""
    with pytest.raises(RuntimeError, match="Sync plan not found"):
        await read_sync_plan(redis_client(FakeRedis()), "missing")


@pytest.mark.asyncio
async def test_commits_sync_state() -> None:
    """Successful plans commit every table signature."""
    fake = FakeRedis()
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.d.t": "100"},
        paths={"p.d.t": ["s3://b/t/data.parquet"]},
    )

    await save_sync_plan(redis_client(fake), plan, 1)
    await commit_sync_state(redis_client(fake), plan)

    assert fake.store[SYNC_STATE_KEY.format(bq_table="p.d.t")] == "100"
    assert SYNC_ACTIVE_KEY not in fake.store


@pytest.mark.asyncio
async def test_reads_and_commits_partition_manifest() -> None:
    """Successful publication replaces the complete physical partition manifest."""
    partition = PhysicalPartition(
        partition_id="0",
        signature="part",
        selection=RangeSelection(partition_id="0", column="cpf", lower=0, upper=10),
    )
    fake = FakeRedis()
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            "p.d.t": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=True,
                current_partitions={"0": partition},
                changed_paths={"0": "s3://b/t/0/data.parquet"},
                removed_partitions={},
            )
        },
    )

    assert await read_partition_manifest(redis_client(fake), "p.d.t") is None
    await commit_sync_state(redis_client(fake), plan)

    result = await read_partition_manifest(redis_client(fake), "p.d.t")
    assert result == PartitionManifest(
        table_signature="table", partitions={"0": partition}
    )
    assert SYNC_PARTITIONS_KEY.format(bq_table="p.d.t") in fake.store


@pytest.mark.asyncio
async def test_completes_task_returns_remaining_count() -> None:
    """Task completion returns the remaining task counter."""
    fake = FakeRedis(decr_value=0)

    assert await complete_task(redis_client(fake), "s1") == 0


@pytest.mark.asyncio
async def test_existing_consumer_group_is_ignored() -> None:
    """Consumer group creation is idempotent."""
    fake = FakeRedisGroup(side_effect=ResponseError("BUSYGROUP"))

    await create_consumer_group(redis_client(fake), "stream", "group")


@pytest.mark.asyncio
async def test_no_active_run_when_flag_is_unset() -> None:
    """An empty Valkey store has no active run."""
    fake = FakeRedis()

    assert await has_active_run(redis_client(fake)) is False


@pytest.mark.asyncio
async def test_active_run_detected_from_flag() -> None:
    """A saved plan's active flag marks its run as still active."""
    fake = FakeRedis()
    fake.store[SYNC_ACTIVE_KEY] = "s1"

    assert await has_active_run(redis_client(fake)) is True


@pytest.mark.asyncio
async def test_trims_stale_stream_entries() -> None:
    """Trimming a stream requests a MINID cutoff derived from the TTL."""
    fake = FakeRedis()

    await trim_stale_entries(redis_client(fake), "dp:sync:tasks", 3_600)

    [(stream, minid)] = fake.xtrim_calls
    assert stream == "dp:sync:tasks"
    assert minid is not None
    assert minid.endswith("-0")
