"""Tests for the FastStream worker orchestrator."""

from unittest.mock import ANY, AsyncMock, patch

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
    TASK_SUB,
    broker,
    cleanup_worker_consumer,
    process_shard,
    worker,
)

TASK = SyncTask(
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
        await process_shard(TASK)

    extract.assert_called_once_with(TASK, db)
    complete.assert_awaited_once_with(ANY, TASK, TaskSuccess())
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
        await process_shard(TASK)

    complete.assert_awaited_once_with(
        ANY,
        TASK,
        TaskFailure(failed_path="s3://bucket/t/data.parquet"),
    )
    publish.assert_not_called()


def test_task_sub_reclaims_only_stale_messages() -> None:
    """The worker uses the configured visibility timeout for auto-claim."""
    assert TASK_SUB.min_idle_time == 900_000


@pytest.mark.asyncio
async def test_worker_shutdown_cleans_its_consumer() -> None:
    """Worker shutdown delegates consumer cleanup to state operations."""
    with patch(
        "dp.sync.worker.cleanup_consumer",
        new_callable=AsyncMock,
    ) as cleanup:
        await cleanup_worker_consumer()

    cleanup.assert_awaited_once_with(
        ANY,
        "dp:sync:tasks",
        "workers",
        ANY,
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
