"""Tests for the FastStream worker orchestrator."""

from unittest.mock import ANY, AsyncMock, call, patch

import pytest
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection

from dp.constants import SYNC_SHUTDOWN_CHANNEL
from dp.models import (
    AllSelection,
    CompletionResult,
    ShutdownMessage,
    SyncTask,
    TaskFailure,
    TaskSuccess,
)
from dp.sync.worker import (
    broker,
    cleanup_worker_consumer,
    process_task,
    subs,
    worker,
)

task = SyncTask(
    sync_id="s1",
    table="p.d.t",
    bucket_path="s3://bucket/t/data.parquet",
    selection=AllSelection(),
)


@pytest.mark.parametrize(
    "should_finalize",
    [False, True],
)
@pytest.mark.asyncio
async def test_process_shard_branches_on_completion_result(
    should_finalize: bool,
) -> None:
    """The worker publishes only for the final unique completion."""
    db = FakeDuckDBConnection()
    with (
        patch("dp.sync.worker.connect", return_value=db),
        patch("dp.sync.worker.extract_task") as extract,
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=CompletionResult(
                first_completion=True,
                remaining=0 if should_finalize else 1,
                should_finalize=should_finalize,
            ),
        ) as complete,
        patch("dp.sync.worker.broker.publish", new_callable=AsyncMock) as publish,
    ):
        await process_task(task)

    extract.assert_called_once_with(task, db)
    complete.assert_awaited_once_with(ANY, task, TaskSuccess())
    assert publish.await_count == (1 if should_finalize else 0)


@pytest.mark.asyncio
async def test_extraction_failure_is_recorded_before_task_completion() -> None:
    """A failed extraction excludes its table from state publication."""
    with (
        patch("dp.sync.worker.connect", return_value=FakeDuckDBConnection()),
        patch("dp.sync.worker.extract_task", side_effect=RuntimeError("failed")),
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=CompletionResult(
                first_completion=True,
                remaining=1,
                should_finalize=False,
            ),
        ) as complete,
        patch("dp.sync.worker.broker.publish", new_callable=AsyncMock) as publish,
    ):
        await process_task(task)

    complete.assert_awaited_once_with(
        ANY,
        task,
        TaskFailure(failed_path="s3://bucket/t/data.parquet"),
    )
    publish.assert_not_called()


def test_task_subscriptions_read_new_and_reclaim_stale_messages() -> None:
    """New tasks use group reads and stale tasks use auto-claim."""
    assert subs["new"].min_idle_time is None
    assert subs["stale"].min_idle_time == 900_000
    assert subs["new"].consumer != subs["stale"].consumer


@pytest.mark.asyncio
async def test_worker_shutdown_cleans_its_consumer() -> None:
    """Worker shutdown delegates consumer cleanup to state operations."""
    with patch(
        "dp.sync.worker.cleanup_consumer",
        new_callable=AsyncMock,
    ) as cleanup:
        await cleanup_worker_consumer()

    cleanup.assert_has_awaits(
        [
            call(ANY, "dp:sync:tasks", "workers", subs["new"].consumer),
            call(ANY, "dp:sync:tasks", "workers", subs["stale"].consumer),
        ]
    )


@pytest.mark.asyncio
async def test_shutdown_message_exits_worker() -> None:
    """The finalizer shutdown broadcast exits each worker."""
    async with TestRedisBroker(broker) as test_broker:
        with patch.object(worker, "exit") as exit_app:
            await test_broker.publish(
                ShutdownMessage(sync_id="s1"),
                SYNC_SHUTDOWN_CHANNEL,
            )

    exit_app.assert_called_once()
