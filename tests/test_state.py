"""Tests for Valkey synchronization state operations."""

from unittest.mock import patch

import pytest
from helpers import FakeRedis, FakeRedisGroup, redis_client, sync_plan
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    ResponseError,
    WatchError,
)

from dp.constants import (
    SYNC_ACTIVE_KEY,
    SYNC_JOB_KEY,
    SYNC_PARTITIONS_KEY,
    SYNC_RUN_TTL_SECONDS,
    SYNC_STATE_KEY,
    SYNC_TASK_RESULTS_KEY,
    SYNC_TRANSACTION_RETRIES,
)
from dp.errors import SyncPlanNotFoundError
from dp.models import (
    AllSelection,
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    RangeSelection,
    SyncStateUpdate,
    SyncTask,
    TaskFailure,
    TaskSuccess,
)
from dp.state import (
    cleanup_consumer,
    commit_sync_state,
    complete_task,
    create_consumer_group,
    create_run,
    decode_redis_value,
    has_pending_finalize_message,
    read_active_sync_id,
    read_failed_paths,
    read_partition_manifest,
    read_remaining_tasks,
    read_sync_plan,
    read_table_signature,
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
async def test_creates_and_reads_required_run() -> None:
    """A run stores its plan, active flag, and task counter together."""
    fake = FakeRedis()
    plan = sync_plan(
        sync_id="s1",
        signatures={"p.d.t": "100"},
        paths={"p.d.t": ["s3://b/t/data.parquet"]},
    )

    assert await create_run(redis_client(fake), plan, 1) is True
    result = await read_sync_plan(redis_client(fake), "s1")

    assert result == plan
    assert fake.store[SYNC_ACTIVE_KEY] == "s1"
    assert fake.store[SYNC_JOB_KEY.format(sync_id="s1")] == "1"
    assert fake.transaction_commands == ["set", "set", "set"]
    assert {expiration for _, _, expiration in fake.set_calls} == {
        SYNC_RUN_TTL_SECONDS,
        None,
    }


@pytest.mark.asyncio
async def test_zero_task_run_has_a_zero_counter() -> None:
    """A deletion-only run stores an explicit zero remaining count."""
    fake = FakeRedis()
    await create_run(redis_client(fake), sync_plan(sync_id="s1"), 0)

    assert fake.store[SYNC_JOB_KEY.format(sync_id="s1")] == "0"
    assert fake.store[SYNC_ACTIVE_KEY] == "s1"


@pytest.mark.asyncio
async def test_run_creation_rejects_active_run() -> None:
    """A producer cannot replace a run that is already active."""
    fake = FakeRedis()
    fake.store[SYNC_ACTIVE_KEY] = "active"

    assert await create_run(redis_client(fake), sync_plan(sync_id="s1"), 1) is False
    assert fake.transaction_commands == []


@pytest.mark.asyncio
async def test_run_creation_retries_watch_conflict() -> None:
    """An active-key watch conflict retries the run creation transaction."""
    fake = FakeRedis(watch_errors=1)
    plan = sync_plan(sync_id="s1")

    assert await create_run(redis_client(fake), plan, 1) is True
    assert fake.store[SYNC_ACTIVE_KEY] == "s1"
    assert fake.transaction_commands == ["set", "set", "set"]


@pytest.mark.asyncio
async def test_missing_plan_fails() -> None:
    """A finalizer cannot infer work without its plan."""
    with pytest.raises(SyncPlanNotFoundError, match="Sync plan not found"):
        await read_sync_plan(redis_client(FakeRedis()), "missing")


@pytest.mark.asyncio
async def test_commits_sync_state() -> None:
    """Successful plans commit every table signature."""
    fake = FakeRedis()
    plan = sync_plan(
        sync_id="s1",
        signatures={"p.d.t": "100"},
        paths={"p.d.t": ["s3://b/t/data.parquet"]},
    )

    await create_run(redis_client(fake), plan, 1)
    await commit_sync_state(
        redis_client(fake),
        plan.sync_id,
        SyncStateUpdate(signatures={"p.d.t": "100"}),
    )

    assert fake.store[SYNC_STATE_KEY.format(bq_table="p.d.t")] == "100"
    assert SYNC_ACTIVE_KEY not in fake.store
    assert fake.transaction_commands == ["set", "delete"]


@pytest.mark.asyncio
async def test_does_not_commit_unpublished_partition_manifest() -> None:
    """An unpublished partitioned table keeps its prior manifest."""
    partition = PhysicalPartition(
        partition_id="0",
        signature="new",
        selection=RangeSelection(partition_id="0", column="cpf", lower=0, upper=10),
    )
    fake = FakeRedis()
    key = SYNC_PARTITIONS_KEY.format(bq_table="p.d.t")
    fake.store[key] = "old"
    plan = sync_plan(
        sync_id="s1",
        partitioned_tables={
            "p.d.t": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=True,
                current_partitions={"0": partition},
                changed_paths={"0": "s3://b/t/0.parquet"},
                removed_partitions={},
            )
        },
    )

    await commit_sync_state(redis_client(fake), plan.sync_id, SyncStateUpdate())

    assert fake.store[key] == "old"


@pytest.mark.asyncio
async def test_reads_and_commits_partition_manifest() -> None:
    """Successful publication replaces the complete physical partition manifest."""
    partition = PhysicalPartition(
        partition_id="0",
        signature="part",
        selection=RangeSelection(partition_id="0", column="cpf", lower=0, upper=10),
    )
    fake = FakeRedis()
    plan = sync_plan(
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
    await commit_sync_state(
        redis_client(fake),
        plan.sync_id,
        SyncStateUpdate(
            partitions={
                "p.d.t": PartitionManifest(
                    table_signature="table", partitions={"0": partition}
                )
            }
        ),
    )

    result = await read_partition_manifest(redis_client(fake), "p.d.t")
    assert result == PartitionManifest(
        table_signature="table", partitions={"0": partition}
    )
    assert SYNC_PARTITIONS_KEY.format(bq_table="p.d.t") in fake.store


@pytest.mark.asyncio
async def test_commits_only_published_tables() -> None:
    """Failed tables keep their old state and stay eligible for retry."""
    fake = FakeRedis()
    failed_key = SYNC_STATE_KEY.format(bq_table="p.d.failed")
    fake.store[failed_key] = "old"
    plan = sync_plan(
        sync_id="s1",
        signatures={"p.d.ok": "new-ok", "p.d.failed": "new-failed"},
        paths={
            "p.d.ok": ["s3://b/ok.parquet"],
            "p.d.failed": ["s3://b/failed.parquet"],
        },
    )

    await commit_sync_state(
        redis_client(fake),
        plan.sync_id,
        SyncStateUpdate(signatures={"p.d.ok": "new-ok"}),
    )

    assert fake.store[SYNC_STATE_KEY.format(bq_table="p.d.ok")] == "new-ok"
    assert fake.store[failed_key] == "old"


def sync_task(path: str = "s3://b/t.parquet", sync_id: str = "s1") -> SyncTask:
    """Return one stable task for completion tests."""
    return SyncTask(
        sync_id=sync_id,
        table="p.d.t",
        bucket_path=path,
        selection=AllSelection(),
    )


@pytest.mark.asyncio
async def test_completes_each_task_once() -> None:
    """Duplicate delivery does not decrement the remaining count twice."""
    fake = FakeRedis()
    fake.store[SYNC_JOB_KEY.format(sync_id="s1")] = "2"
    task = sync_task()
    outcome = TaskSuccess()

    first = await complete_task(redis_client(fake), task, outcome)
    duplicate = await complete_task(redis_client(fake), task, outcome)

    assert first.remaining == 1
    assert first.should_finalize is False
    assert duplicate.remaining == 1
    assert fake.store[SYNC_JOB_KEY.format(sync_id="s1")] == "1"


@pytest.mark.asyncio
async def test_final_unique_completion_requests_finalization() -> None:
    """Only the final unique task requests finalization."""
    fake = FakeRedis()
    fake.store[SYNC_JOB_KEY.format(sync_id="s1")] = "1"

    result = await complete_task(
        redis_client(fake),
        sync_task(),
        TaskSuccess(),
    )

    assert result.should_finalize is True


@pytest.mark.asyncio
async def test_records_failed_path_in_typed_outcome() -> None:
    """The finalizer can read a failed task path from the result hash."""
    fake = FakeRedis()
    fake.store[SYNC_JOB_KEY.format(sync_id="s1")] = "1"
    path = "s3://b/failed.parquet"

    await complete_task(
        redis_client(fake),
        sync_task(path),
        TaskFailure(failed_path=path),
    )

    assert await read_failed_paths(redis_client(fake), "s1") == {path}
    assert SYNC_TASK_RESULTS_KEY.format(sync_id="s1") in fake.hashes


@pytest.mark.asyncio
async def test_ignores_successful_task_outcome_when_reading_failures() -> None:
    """A successful task outcome has no failed path."""
    fake = FakeRedis()
    fake.store[SYNC_JOB_KEY.format(sync_id="s1")] = "1"

    await complete_task(redis_client(fake), sync_task(), TaskSuccess())

    assert await read_failed_paths(redis_client(fake), "s1") == set()


@pytest.mark.asyncio
async def test_completion_retries_watch_conflict_with_fixed_commands() -> None:
    """A watch conflict retries one fixed transaction command sequence."""
    remaining_key = SYNC_JOB_KEY.format(sync_id="s1")
    fake = FakeRedis(
        watch_errors=1,
        conflict_store_updates={remaining_key: "1"},
    )
    fake.store[remaining_key] = "2"

    result = await complete_task(
        redis_client(fake),
        sync_task(),
        TaskSuccess(),
    )

    assert result.remaining == 0
    assert result.should_finalize is True
    assert fake.store[remaining_key] == "0"
    assert fake.transaction_commands == ["hset", "set", "expire"]


@pytest.mark.asyncio
async def test_completion_requires_task_counter() -> None:
    """A completion cannot infer a missing remaining count."""
    with pytest.raises(RuntimeError, match="counter not found"):
        await complete_task(
            redis_client(FakeRedis()),
            sync_task(),
            TaskSuccess(),
        )


@pytest.mark.asyncio
async def test_completion_rejects_exhausted_counter() -> None:
    """A new task cannot complete after the counter reaches zero."""
    fake = FakeRedis()
    fake.store[SYNC_JOB_KEY.format(sync_id="s1")] = "0"

    with pytest.raises(RuntimeError, match="Invalid task counter"):
        await complete_task(
            redis_client(fake),
            sync_task(),
            TaskSuccess(),
        )


@pytest.mark.asyncio
async def test_completion_stops_after_bounded_watch_conflicts() -> None:
    """Persistent transaction conflicts fail after the configured limit."""
    fake = FakeRedis(watch_errors=SYNC_TRANSACTION_RETRIES)
    fake.store[SYNC_JOB_KEY.format(sync_id="s1")] = "1"

    with pytest.raises(WatchError):
        await complete_task(
            redis_client(fake),
            sync_task(),
            TaskSuccess(),
        )


@pytest.mark.asyncio
async def test_deletes_idle_consumer() -> None:
    """A consumer with no pending messages is deleted."""
    fake = FakeRedis()

    await cleanup_consumer(redis_client(fake), "stream", "group", "worker")

    assert fake.deleted_consumers == [("stream", "group", "worker")]


@pytest.mark.asyncio
async def test_keeps_consumer_with_pending_messages() -> None:
    """A consumer with pending messages remains for later reclaim."""
    fake = FakeRedis(pending_consumers={"worker"})

    await cleanup_consumer(redis_client(fake), "stream", "group", "worker")

    assert fake.deleted_consumers == []


@pytest.mark.asyncio
async def test_cleanup_logs_and_ignores_valkey_error() -> None:
    """A Valkey cleanup failure is visible but does not block shutdown."""
    fake = FakeRedis(cleanup_error=RedisConnectionError())

    with patch("dp.state.logger.warning") as warning:
        await cleanup_consumer(redis_client(fake), "stream", "group", "worker")

    warning.assert_called_once()


@pytest.mark.asyncio
async def test_existing_consumer_group_is_ignored() -> None:
    """Consumer group creation is idempotent."""
    fake = FakeRedisGroup(side_effect=ResponseError("BUSYGROUP"))

    await create_consumer_group(redis_client(fake), "stream", "group")


@pytest.mark.asyncio
async def test_reads_active_sync_id() -> None:
    """The active run ID is read from the active flag."""
    fake = FakeRedis()
    fake.store[SYNC_ACTIVE_KEY] = "s1"

    assert await read_active_sync_id(redis_client(fake)) == "s1"
    assert await read_active_sync_id(redis_client(FakeRedis())) is None


@pytest.mark.asyncio
async def test_reads_remaining_task_count() -> None:
    """The remaining task count is read from the run counter."""
    fake = FakeRedis()
    await create_run(redis_client(fake), sync_plan(sync_id="s1"), 3)

    assert await read_remaining_tasks(redis_client(fake), "s1") == 3


@pytest.mark.asyncio
async def test_missing_task_counter_fails() -> None:
    """A missing task counter is reported instead of guessed."""
    with pytest.raises(RuntimeError, match="Task counter not found"):
        await read_remaining_tasks(redis_client(FakeRedis()), "s1")


@pytest.mark.asyncio
async def test_pending_finalize_message_returns_false_when_empty() -> None:
    """An idle finalizer stream has no pending finalizer message."""
    fake = FakeRedis()

    assert await has_pending_finalize_message(redis_client(fake)) is False


@pytest.mark.asyncio
async def test_pending_finalize_message_detected_in_group() -> None:
    """A pending finalizer message keeps a run from being re-published."""
    fake = FakeRedis(pending_groups={("dp:sync:finalize", "finalizers")})

    assert await has_pending_finalize_message(redis_client(fake)) is True


@pytest.mark.asyncio
async def test_trims_stale_stream_entries() -> None:
    """Trimming a stream requests a MINID cutoff derived from the TTL."""
    fake = FakeRedis()

    await trim_stale_entries(redis_client(fake), "dp:sync:tasks", 3_600)

    [(stream, minid)] = fake.xtrim_calls
    assert stream == "dp:sync:tasks"
    assert minid is not None
    assert minid.endswith("-0")
